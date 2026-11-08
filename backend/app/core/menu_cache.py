"""门店菜单聚合读侧缓存（Task 12）。

- bundle：分类 + 在售餐品一次成型的 JSON，TTL 60s 兜底，写路径主动失效。
- version：门店单调递增版本戳（INCR），客户端携带相同版本时返回 changed:false 小响应。
- 缓存击穿：SET NX EX 互斥锁，避免并发重建压垮 DB。
- 缓存雪崩：TTL 加 ±20% 随机偏移。
- 版本 fencing：bundle、version、lock 共用 hash slot，Lua 原子校验 owner 与版本后写入。
- 大Key防护：bundle JSON 超 256KB 时跳过缓存。
Redis 命令异常在本模块按缓存未命中/锁不可用处理，不向调用方伪造有效版本。
菜单聚合读服务必须在无法验证远端 generation 且未持有重建锁时 fail-closed
（HTTP 503），不能直接返回进程内旧 bundle，也不能让每个 Worker 无锁回源。
这与普通非关键缓存的查库降级策略不同，是为避免跨 Worker 菜单陈旧和 DB 惊群。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from typing import Any

from app.core.cache_keys import (
    MENU_BUNDLE_TTL_SECONDS,
    menu_bundle_key,
    menu_rebuild_lock_key,
    menu_schedule_key,
    menu_version_key,
    ttl_with_jitter,
)
from app.core.redis_client import delete_with_retry, get_redis

logger = logging.getLogger(__name__)

MENU_TTL_SECONDS = MENU_BUNDLE_TTL_SECONDS
_BUNDLE_MAX_BYTES = 256 * 1024  # bundle JSON 超 256KB 跳过缓存以避免大Key
_REBUILD_LOCK_TTL = 10  # 重建锁租约；owner token 防止过期任务误删/覆盖新 owner

_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

_RENEW_LOCK_SCRIPT = """
-- MENU_RENEW_LOCK
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

_SET_BUNDLE_IF_OWNER_SCRIPT = """
-- MENU_BUNDLE_IF_OWNER_AND_VERSION
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
if redis.call('GET', KEYS[3]) ~= ARGV[4] then
    return 0
end
return redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
"""

_GET_BUNDLE_WITH_VERSION_SCRIPT = """
-- MENU_GET_BUNDLE_WITH_VERSION
local bundle = redis.call('GET', KEYS[1])
if not bundle then
    return nil
end
local version = redis.call('GET', KEYS[2])
if not version then
    return nil
end
return {bundle, version}
"""

_SYNC_MENU_SCHEDULE_SCRIPT = """
-- MENU_SYNC_SCHEDULE_GENERATION
local desired_schedule = ARGV[1]
local current_schedule = redis.call('GET', KEYS[1])
local current_version = redis.call('GET', KEYS[2])
if current_schedule == desired_schedule and current_version then
    return current_version
end
-- INCR 必须先成功，才能提交新的 schedule marker；OOM 时脚本在这里终止，
-- 保留旧 marker 以便后续请求重试，绝不把新时段伪装成旧版本。
local version = redis.call('INCR', KEYS[2])
redis.call('SET', KEYS[1], desired_schedule)
redis.call('DEL', KEYS[3])
return version
"""


async def sync_menu_schedule_generation(
    store_id: int, active_schedule_id: int | None
) -> int | None:
    """持久化当前时段并在切换时原子递增菜单版本、删除旧 bundle。

    不依赖短 TTL bundle 是否仍存在；因此缓存过期、冷 Worker 首次请求、
    午/晚市边界都能正确触发客户端版本变化。Redis/OOM 失败返回 None，
    调用方必须 fail-closed，不能用旧版本继续响应。
    """
    schedule_token = str(int(active_schedule_id)) if active_schedule_id is not None else "none"
    try:
        r = await get_redis()
        raw = await r.eval(
            _SYNC_MENU_SCHEDULE_SCRIPT,
            3,
            menu_schedule_key(store_id),
            menu_version_key(store_id),
            menu_bundle_key(store_id),
            schedule_token,
        )
        version = int(raw or 0)
        return version if version > 0 else None
    except Exception:
        logger.warning(
            "同步菜单时段版本失败，拒绝返回未经版本确认的菜单 store=%s schedule=%s",
            store_id,
            schedule_token,
            exc_info=True,
        )
        return None


async def get_menu_version(
    store_id: int,
    *,
    sync_schedule: bool = False,
    active_schedule_id: int | None = None,
) -> int | None:
    """读取菜单版本；菜单聚合读路径可同时原子同步当前时段代际。"""
    if sync_schedule:
        return await sync_menu_schedule_generation(store_id, active_schedule_id)
    try:
        r = await get_redis()
        raw = await r.get(menu_version_key(store_id))
        return int(raw or 0)
    except Exception:
        return 0


_ENSURE_MENU_GENERATION_SCRIPT = """
-- MENU_ENSURE_DURABLE_GENERATION
local current = redis.call('GET', KEYS[1])
local desired = tonumber(ARGV[1])
if not current or tonumber(current) < desired then
    redis.call('SET', KEYS[1], ARGV[1])
    redis.call('DEL', KEYS[2])
    return desired
end
if tonumber(current) > desired then
    return -1
end
return tonumber(current)
"""


async def ensure_menu_generation(store_id: int, generation: int) -> int | None:
    """Mirror a durable MySQL generation into Redis without allowing stale regression."""
    desired = int(generation)
    if desired <= 0:
        return None
    try:
        r = await get_redis()
        result = await r.eval(
            _ENSURE_MENU_GENERATION_SCRIPT,
            2,
            menu_version_key(store_id),
            menu_bundle_key(store_id),
            str(desired),
        )
        value = int(result or 0)
        return value if value == desired else None
    except Exception:
        logger.warning(
            "恢复菜单 Redis generation 失败 store=%s generation=%s",
            store_id, desired, exc_info=True,
        )
        return None


async def bump_menu_version(store_id: int) -> int:
    """递增持久版本并删除聚合缓存。

    版本键不设置 TTL：它是客户端版本戳，过期重置可能让旧客户端误判 changed:false。
    版本键与 bundle/lock 共用 Redis Cluster hash slot，支持 Lua 原子 fencing。
    """
    try:
        r = await get_redis()
        key = menu_version_key(store_id)
        version = int(await r.incr(key))
        await delete_with_retry(r, menu_bundle_key(store_id))
        return version
    except Exception:
        # noeviction 模式下，Redis 达到 maxmemory 会拒绝 INCR 等写命令，
        # 但 DEL 仍可执行。即使版本递增失败，也必须尽力删除旧 bundle，
        # 否则旧版本戳 + 旧 bundle 会继续匹配，导致菜单变更被缓存遮蔽。
        logger.warning(
            "菜单版本递增失败，尝试仅删除聚合缓存 store=%s",
            store_id,
            exc_info=True,
        )
        try:
            r = await get_redis()
            await delete_with_retry(r, menu_bundle_key(store_id))
        except Exception:
            logger.warning(
                "Redis 不可用，菜单聚合缓存无法主动删除 store=%s",
                store_id,
                exc_info=True,
            )
        return 0


async def get_menu_bundle(store_id: int) -> dict[str, Any] | None:
    """原子读取 bundle + 当前版本，只返回版本匹配的缓存快照。"""
    try:
        r = await get_redis()
        result = await r.eval(
            _GET_BUNDLE_WITH_VERSION_SCRIPT,
            2,
            menu_bundle_key(store_id),
            menu_version_key(store_id),
        )
        if not result or len(result) != 2:
            return None
        data = json.loads(result[0])
        if not isinstance(data, dict):
            return None
        if int(data.get("version") or 0) != int(result[1]):
            return None
        return data
    except Exception:
        return None


async def set_menu_bundle(
    store_id: int,
    bundle: dict[str, Any],
    *,
    owner_token: str | None = None,
    expected_version: int | None = None,
) -> bool:
    """仅当 owner 与菜单版本均未变化时写入缓存，防止旧构建覆盖新菜单。"""
    if not owner_token:
        return False
    version = int(expected_version or bundle.get("version") or 0)
    if version <= 0:
        return False
    try:
        r = await get_redis()
        from app.core.response import dumps_json

        raw = dumps_json(bundle, compact=True)
        if len(raw.encode("utf-8")) > _BUNDLE_MAX_BYTES:
            logger.warning(
                "menu bundle 过大 (store=%s, %dKB)，跳过缓存以避免大Key",
                store_id, len(raw.encode("utf-8")) // 1024,
            )
            return False
        result = await r.eval(
            _SET_BUNDLE_IF_OWNER_SCRIPT,
            3,
            menu_rebuild_lock_key(store_id),
            menu_bundle_key(store_id),
            menu_version_key(store_id),
            owner_token,
            raw,
            ttl_with_jitter(MENU_TTL_SECONDS),
            str(version),
        )
        return bool(result)
    except Exception:
        return False


async def renew_rebuild_lock(store_id: int, *, owner_token: str | None = None) -> bool:
    """仅当前 owner 可续租菜单重建锁。"""
    if not owner_token:
        return False
    try:
        r = await get_redis()
        return bool(
            await r.eval(
                _RENEW_LOCK_SCRIPT,
                1,
                menu_rebuild_lock_key(store_id),
                owner_token,
                _REBUILD_LOCK_TTL,
            )
        )
    except Exception:
        logger.debug("菜单重建锁续租失败 store=%s", store_id, exc_info=True)
        return False


async def maintain_rebuild_lock(
    store_id: int,
    owner_token: str,
    *,
    owner_task: asyncio.Task[Any] | None,
) -> None:
    """后台续租；由调用方传入请求任务，任务结束后停止续租。"""
    # 注意：current_task() 在本协程内只会返回心跳自身，不能代表发起请求的 task。
    interval = max(1, _REBUILD_LOCK_TTL // 3)
    while True:
        await asyncio.sleep(interval)
        if owner_task is None or owner_task.done():
            logger.warning("菜单重建锁持有者任务已结束，停止心跳 store=%s", store_id)
            return
        if not await renew_rebuild_lock(store_id, owner_token=owner_token):
            return


async def try_acquire_rebuild_lock(store_id: int) -> str | None:
    """缓存击穿保护：返回唯一 owner token；竞争/Redis 故障返回 None。"""
    try:
        r = await get_redis()
        token = uuid.uuid4().hex
        acquired = await r.set(menu_rebuild_lock_key(store_id), token, nx=True, ex=_REBUILD_LOCK_TTL)
        return token if acquired else None
    except Exception:
        return None


async def release_rebuild_lock(store_id: int, *, owner_token: str | None = None) -> None:
    if not owner_token:
        return
    try:
        r = await get_redis()
        await r.eval(_RELEASE_LOCK_SCRIPT, 1, menu_rebuild_lock_key(store_id), owner_token)
    except Exception:
        pass


async def wait_for_rebuild(store_id: int, timeout: float = 0.3) -> dict[str, Any] | None:
    """有界轮询菜单缓存；等待者不回源重建，避免锁竞争时形成数据库惊群。"""
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(timeout))
        while True:
            cached = await get_menu_bundle(store_id)
            if cached is not None:
                return cached
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(random.uniform(0.04, 0.10), remaining))
    except Exception:
        return None

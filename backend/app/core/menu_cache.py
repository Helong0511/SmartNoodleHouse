"""门店菜单聚合读侧缓存（Task 12）。

- bundle：分类 + 在售餐品一次成型的 JSON，TTL 60s 兜底，写路径主动失效。
- version：门店单调递增版本戳（INCR），客户端携带相同版本时返回 changed:false 小响应。
- 缓存击穿：SET NX EX 互斥锁，避免并发重建压垮 DB。
- 缓存雪崩：TTL 加 ±20% 随机偏移。
- 版本 fencing：bundle、version、lock 共用 hash slot，Lua 原子校验 owner 与版本后写入。
- 大Key防护：bundle JSON 超 256KB 时跳过缓存。
Redis 全部异常静默降级为未缓存，不阻塞点餐主流程。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.core.cache_keys import (
    MENU_BUNDLE_TTL_SECONDS,
    menu_bundle_key,
    menu_rebuild_lock_key,
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


async def get_menu_version(store_id: int) -> int:
    """读取持久化菜单版本；兼容迁移旧版 30 天 TTL 版本键。"""
    try:
        r = await get_redis()
        key = menu_version_key(store_id)
        raw = await r.get(key)
        if raw is None:
            # 旧键为 snh:menu:ver:{store_id}。首次访问时原子迁移到同 hash slot 的新键。
            legacy = await r.get(f"snh:menu:ver:{int(store_id)}")
            if legacy is not None:
                await r.set(key, str(legacy), nx=True)
                raw = await r.get(key)
        return int(raw or 0)
    except Exception:
        return 0


async def bump_menu_version(store_id: int) -> int:
    """递增持久版本并删除聚合缓存。

    版本键不设置 TTL：它是客户端版本戳，过期重置可能让旧客户端误判 changed:false。
    版本键与 bundle/lock 共用 Redis Cluster hash slot，支持 Lua 原子 fencing。
    """
    try:
        r = await get_redis()
        key = menu_version_key(store_id)
        if await r.get(key) is None:
            legacy = await r.get(f"snh:menu:ver:{int(store_id)}")
            if legacy is not None:
                await r.set(key, str(legacy), nx=True)
        version = int(await r.incr(key))
        await delete_with_retry(r, menu_bundle_key(store_id))
        return version
    except Exception:
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
    """未抢到锁的调用方短暂等待后重读缓存。"""
    try:
        await asyncio.sleep(timeout)
        return await get_menu_bundle(store_id)
    except Exception:
        return None

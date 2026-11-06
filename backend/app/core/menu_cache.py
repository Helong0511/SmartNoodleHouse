"""门店菜单聚合读侧缓存（Task 12）。

- bundle：分类 + 在售餐品一次成型的 JSON，TTL 60s 兜底，写路径主动失效。
- version：门店单调递增版本戳（INCR），客户端携带相同版本时返回 changed:false 小响应。
- 缓存击穿：SET NX EX 互斥锁，避免并发重建压垮 DB。
- 缓存雪崩：TTL 加 ±20% 随机偏移。
- 延迟双删：bump_menu_version 删 bundle 后延迟再删一次。
- 大Key防护：bundle JSON 超 256KB 时跳过缓存。
Redis 全部异常静默降级为未缓存，不阻塞点餐主流程。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from app.core.async_utils import spawn_bg
from app.core.cache_keys import (
    MENU_BUNDLE_TTL_SECONDS,
    MENU_VERSION_TTL_SECONDS,
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
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
end
return 0
"""


async def get_menu_version(store_id: int) -> int:
    try:
        r = await get_redis()
        raw = await r.get(menu_version_key(store_id))
        return int(raw or 0)
    except Exception:
        return 0


async def bump_menu_version(store_id: int) -> int:
    """菜单变更时递增版本并删除聚合缓存；返回新版本号（Redis 不可用时返回 0）。

    延迟双删：先删 bundle，0.1s 后再删一次，避免并发读写时刚写入的旧值残留。
    """
    try:
        r = await get_redis()
        version = int(await r.incr(menu_version_key(store_id)))
        await r.expire(menu_version_key(store_id), MENU_VERSION_TTL_SECONDS)
        await delete_with_retry(r, menu_bundle_key(store_id))
        # 延迟双删：防止删后又有并发请求写入旧 bundle
        spawn_bg(_delayed_delete(menu_bundle_key(store_id), 0.1))
        return version
    except Exception:
        return 0


async def _delayed_delete(key: str, delay: float) -> None:
    """延迟再删一次，防止并发写窗口中旧值残留。"""
    try:
        await asyncio.sleep(delay)
        r = await get_redis()
        await r.delete(key)
    except Exception:
        pass


async def get_menu_bundle(store_id: int) -> dict[str, Any] | None:
    """读取菜单缓存。缓存穿透防护：无额外空值标记，因 build_menu_bundle
    对不存在的 store 返回空分类/空餐品的合法 bundle，空 bundle 本身会被缓存。"""
    try:
        r = await get_redis()
        raw = await r.get(menu_bundle_key(store_id))
        if raw:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
    except Exception:
        pass
    return None


async def set_menu_bundle(store_id: int, bundle: dict[str, Any], *, owner_token: str | None = None) -> None:
    if not owner_token:
        return
    try:
        r = await get_redis()
        from app.core.response import dumps_json

        raw = dumps_json(bundle, compact=True)
        if len(raw.encode("utf-8")) > _BUNDLE_MAX_BYTES:
            logger.warning(
                "menu bundle 过大 (store=%s, %dKB)，跳过缓存以避免大Key",
                store_id, len(raw.encode("utf-8")) // 1024,
            )
            return
        await r.eval(
            _SET_BUNDLE_IF_OWNER_SCRIPT,
            2,
            menu_rebuild_lock_key(store_id),
            menu_bundle_key(store_id),
            owner_token,
            raw,
            ttl_with_jitter(MENU_TTL_SECONDS),
        )
    except Exception:
        pass


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

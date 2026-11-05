"""推荐结果 Redis 缓存层（Task 8）。

- 仅缓存接口响应 payload（瘦身字段后的 dict），TTL ≤ 120s；
- Redis 不可用时所有操作静默降级，不影响推荐主流程；
- 画像更新 / 下单 / 评价后调用 invalidate_customer 主动失效；
- 缓存击穿：SET NX EX 互斥锁，避免并发重建压垮 DB；
- 缓存雪崩：TTL 加 ±20% 随机偏移；
- 延迟双删：invalidate_customer 删后延迟再删一次。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.core.async_utils import spawn_bg
from app.core.cache_keys import (
    RECO_TTL_SECONDS,
    reco_customer_pattern,
    reco_key,
    reco_rebuild_lock_key,
    ttl_with_jitter,
)
from app.core.redis_client import delete_with_retry, get_redis, release_owned_lock
from app.core.response import dumps_json

logger = logging.getLogger(__name__)

_REBUILD_LOCK_TTL = 10  # 重建锁最多持 10s


async def get_cached_reco(
    customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str = ""
) -> dict[str, Any] | None:
    """读取推荐缓存。缓存穿透防护：无额外空值标记，因推荐服务对任意 customer/store
    都会返回合法 payload（冷启动时返回热门餐品），空结果本身会被缓存。"""
    try:
        r = await get_redis()
        raw = await r.get(reco_key(customer_id, store_id, scene, bucket, fingerprint))
        if raw:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                payload["cache_hit"] = True
                return payload
    except Exception:  # noqa: BLE001 - 缓存不可用必须降级
        logger.debug("推荐缓存读取失败，降级直算", exc_info=True)
    return None


async def set_cached_reco(
    customer_id: int | None,
    store_id: int,
    scene: str,
    bucket: str,
    payload: dict[str, Any],
    fingerprint: str = "",
) -> None:
    try:
        r = await get_redis()
        body = dict(payload)
        body["cache_hit"] = False
        await r.set(
            reco_key(customer_id, store_id, scene, bucket, fingerprint),
            dumps_json(body, compact=True),
            ex=ttl_with_jitter(RECO_TTL_SECONDS),
        )
    except Exception:  # noqa: BLE001
        logger.debug("推荐缓存写入失败，忽略", exc_info=True)


async def invalidate_customer(customer_id: int) -> None:
    """失效某顾客全部场景/桶位的推荐结果。延迟双删防止并发写窗口旧值残留。"""
    if not customer_id:
        return
    pattern = reco_customer_pattern(customer_id)
    try:
        r = await get_redis()
        keys = []
        async for k in r.scan_iter(match=pattern, count=200):
            keys.append(k)
        if keys:
            await delete_with_retry(r, *keys)
        # 延迟双删：防止删后又有并发请求写入旧推荐结果
        spawn_bg(_delayed_invalidate(pattern))
    except Exception:  # noqa: BLE001
        logger.debug("推荐缓存失效失败(customer=%s)，等待 TTL", customer_id, exc_info=True)


async def _delayed_invalidate(pattern: str) -> None:
    """延迟再删一次，防止并发写窗口中旧值残留。"""
    try:
        await asyncio.sleep(0.1)
        r = await get_redis()
        keys = []
        async for k in r.scan_iter(match=pattern, count=200):
            keys.append(k)
        if keys:
            await r.delete(*keys)
    except Exception:
        pass


async def try_acquire_reco_lock(
    customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str = ""
) -> str | None:
    """缓存击穿保护：返回唯一持有者 token；失败/Redis 不可用返回 None。"""
    try:
        r = await get_redis()
        token = __import__("uuid").uuid4().hex
        acquired = await r.set(
            reco_rebuild_lock_key(customer_id, store_id, scene, bucket, fingerprint),
            token, nx=True, ex=_REBUILD_LOCK_TTL,
        )
        return token if acquired else None
    except Exception:
        return None


async def release_reco_lock(
    customer_id: int | None, store_id: int, scene: str, bucket: str,
    fingerprint: str = "", token: str | None = None,
) -> None:
    """只释放当前调用方持有的锁，避免误删后续持有者的锁。"""
    if not token:
        return
    try:
        r = await get_redis()
        await release_owned_lock(
            r, reco_rebuild_lock_key(customer_id, store_id, scene, bucket, fingerprint), token
        )
    except Exception:
        pass


async def wait_for_reco(
    customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str = "",
    timeout: float = 0.3,
) -> dict[str, Any] | None:
    """未抢到锁的调用方短暂等待后重读缓存。"""
    try:
        await asyncio.sleep(timeout)
        return await get_cached_reco(customer_id, store_id, scene, bucket, fingerprint)
    except Exception:
        return None

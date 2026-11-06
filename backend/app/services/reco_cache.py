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
import uuid
from typing import Any

from app.core.async_utils import spawn_bg
from app.core.cache_keys import (
    RECO_TTL_SECONDS,
    reco_customer_lock_pattern,
    reco_customer_pattern,
    reco_key,
    reco_rebuild_lock_key,
    ttl_with_jitter,
)
from app.core.redis_client import delete_with_retry, get_redis
from app.core.response import dumps_json

logger = logging.getLogger(__name__)

_REBUILD_LOCK_TTL = 30  # 推荐重建最长租约；超时后旧 owner 不能释放/覆盖新 owner

_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

_RENEW_LOCK_SCRIPT = """
-- RECO_RENEW_LOCK
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

_SET_CACHE_IF_OWNER_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
end
return 0
"""


async def get_cached_reco(
    customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str = "", limit: int = 10
) -> dict[str, Any] | None:
    """读取推荐缓存。缓存穿透防护：无额外空值标记，因推荐服务对任意 customer/store
    都会返回合法 payload（冷启动时返回热门餐品），空结果本身会被缓存。"""
    try:
        r = await get_redis()
        raw = await r.get(reco_key(customer_id, store_id, scene, bucket, fingerprint, limit))
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
    *,
    owner_token: str | None = None,
    limit: int = 10,
) -> None:
    """仅允许当前锁 owner 写入缓存，避免过期旧任务覆盖新结果。

    缓存键与锁键共享 Redis Cluster hash tag，因此 Lua 可在单 slot 原子执行。
    未持有锁（Redis 故障或锁竞争超时后降级直算）的请求不写缓存。
    """
    if not owner_token:
        return
    try:
        r = await get_redis()
        body = dict(payload)
        body["cache_hit"] = False
        ttl = ttl_with_jitter(RECO_TTL_SECONDS)
        result = await r.eval(
            _SET_CACHE_IF_OWNER_SCRIPT,
            2,
            reco_rebuild_lock_key(customer_id, store_id, scene, bucket, fingerprint, limit),
            reco_key(customer_id, store_id, scene, bucket, fingerprint, limit),
            owner_token,
            dumps_json(body, compact=True),
            ttl,
        )
        if not result:
            logger.debug("推荐缓存写入跳过：重建租约已丢失")
    except Exception:  # noqa: BLE001
        logger.debug("推荐缓存写入失败，忽略", exc_info=True)


async def invalidate_customer(customer_id: int) -> None:
    """失效某顾客全部场景/桶位的推荐结果。延迟双删防止并发写窗口旧值残留。"""
    if not customer_id:
        return
    patterns = (
        reco_customer_pattern(customer_id),
        reco_customer_lock_pattern(customer_id),
    )
    try:
        r = await get_redis()
        keys = []
        for pattern in patterns:
            async for k in r.scan_iter(match=pattern, count=200):
                keys.append(k)
        if keys:
            # 同时删除缓存和租约：在途旧 owner 后续 Lua 写入/释放均会因 token 不匹配而失败。
            # Cluster 多键 DEL 要求同一 hash slot；顾客的门店/场景键跨 slot，逐键删除。
            for key in sorted(set(keys)):
                await delete_with_retry(r, key)
        # 延迟再清缓存值（不清锁），兜底处理扫描/删除期间的并发写入。
        spawn_bg(_delayed_invalidate(patterns[0]))
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
            # SCAN 可能命中多个 Cluster slot，不能跨 slot 执行多键 DEL。
            for key in sorted(set(keys)):
                await delete_with_retry(r, key)
    except Exception:
        pass


async def try_acquire_reco_lock(
    customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str = "", limit: int = 10
) -> str | None:
    """缓存击穿保护：返回唯一 owner token；锁竞争/Redis 故障均返回 None 并允许降级直算。"""
    token = uuid.uuid4().hex
    try:
        r = await get_redis()
        acquired = await r.set(
            reco_rebuild_lock_key(customer_id, store_id, scene, bucket, fingerprint, limit),
            token,
            nx=True,
            ex=_REBUILD_LOCK_TTL,
        )
        return token if acquired else None
    except Exception:
        logger.debug("推荐重建锁不可用，降级直算", exc_info=True)
        return None


async def renew_reco_lock(
    customer_id: int | None,
    store_id: int,
    scene: str,
    bucket: str,
    fingerprint: str = "",
    *,
    owner_token: str | None = None,
    limit: int = 10,
) -> bool:
    """仅当前 owner 可续租推荐重建锁。"""
    if not owner_token:
        return False
    try:
        r = await get_redis()
        return bool(
            await r.eval(
                _RENEW_LOCK_SCRIPT,
                1,
                reco_rebuild_lock_key(customer_id, store_id, scene, bucket, fingerprint, limit),
                owner_token,
                _REBUILD_LOCK_TTL,
            )
        )
    except Exception:
        logger.debug("推荐重建锁续租失败 store=%s scene=%s", store_id, scene, exc_info=True)
        return False


async def maintain_reco_lock(
    customer_id: int | None,
    store_id: int,
    scene: str,
    bucket: str,
    fingerprint: str = "",
    *,
    owner_token: str,
    limit: int = 10,
) -> None:
    """后台续租；租约丢失后停止，旧 owner 写缓存仍受 Lua fencing 保护。"""
    interval = max(1, _REBUILD_LOCK_TTL // 3)
    while True:
        await asyncio.sleep(interval)
        if not await renew_reco_lock(
            customer_id, store_id, scene, bucket, fingerprint,
            owner_token=owner_token, limit=limit,
        ):
            return


async def release_reco_lock(
    customer_id: int | None,
    store_id: int,
    scene: str,
    bucket: str,
    fingerprint: str = "",
    *,
    owner_token: str | None = None,
    limit: int = 10,
) -> None:
    """仅允许当前 owner 释放锁，防止旧请求删除后来者的租约。"""
    if not owner_token:
        return
    try:
        r = await get_redis()
        await r.eval(
            _RELEASE_LOCK_SCRIPT,
            1,
            reco_rebuild_lock_key(customer_id, store_id, scene, bucket, fingerprint, limit),
            owner_token,
        )
    except Exception:
        logger.debug("推荐重建锁释放失败，等待租约过期", exc_info=True)


async def wait_for_reco(
    customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str = "",
    timeout: float = 0.3,
    limit: int = 10,
) -> dict[str, Any] | None:
    """未抢到锁的调用方短暂等待后重读缓存。"""
    try:
        await asyncio.sleep(timeout)
        return await get_cached_reco(customer_id, store_id, scene, bucket, fingerprint, limit)
    except Exception:
        return None

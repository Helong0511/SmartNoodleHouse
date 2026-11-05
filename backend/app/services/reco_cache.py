"""推荐结果 Redis 缓存层（Task 8）。

- 仅缓存接口响应 payload（瘦身字段后的 dict），TTL ≤ 120s；
- Redis 不可用时优先使用短 TTL 本地缓存；无缓存且无法获得分布式锁时由接口返回可重试 503，避免并发回源；
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
import time
from collections import OrderedDict
from threading import RLock
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

_REBUILD_LOCK_TTL = 30  # 30s 租约；唯一 token 防止过期持有者误删新锁

# Redis 故障时的短 TTL 进程内兜底缓存：配合 local_singleflight 防止同 worker 重复重算。
# 仅保存推荐响应（短至 2 秒），有界 LRU；客户画像/订单变化时按 customer_id 主动失效。
_LOCAL_RECO_TTL = 2.0
_LOCAL_RECO_MAX = 512
_LOCAL_RECO_CACHE: OrderedDict[tuple, tuple[float, dict[str, Any]]] = OrderedDict()
_LOCAL_RECO_GUARD = RLock()


def _local_reco_key(
    customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str
) -> tuple:
    return (int(customer_id) if customer_id is not None else None, int(store_id),
            str(scene), str(bucket), str(fingerprint or ""))


def _get_local_reco(key: tuple) -> dict[str, Any] | None:
    now = time.monotonic()
    with _LOCAL_RECO_GUARD:
        item = _LOCAL_RECO_CACHE.get(key)
        if item is None:
            return None
        expires_at, payload = item
        if expires_at <= now:
            _LOCAL_RECO_CACHE.pop(key, None)
            return None
        _LOCAL_RECO_CACHE.move_to_end(key)
        return {**payload, "cache_hit": True}


def _set_local_reco(key: tuple, payload: dict[str, Any]) -> None:
    with _LOCAL_RECO_GUARD:
        _LOCAL_RECO_CACHE[key] = (time.monotonic() + _LOCAL_RECO_TTL, dict(payload))
        _LOCAL_RECO_CACHE.move_to_end(key)
        while len(_LOCAL_RECO_CACHE) > _LOCAL_RECO_MAX:
            _LOCAL_RECO_CACHE.popitem(last=False)


def _invalidate_local_reco_customer(customer_id: int) -> None:
    cid = int(customer_id)
    with _LOCAL_RECO_GUARD:
        for key in list(_LOCAL_RECO_CACHE):
            if key[0] == cid:
                _LOCAL_RECO_CACHE.pop(key, None)



async def get_cached_reco(
    customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str = ""
) -> dict[str, Any] | None:
    """读取推荐缓存。缓存穿透防护：无额外空值标记，因推荐服务对任意 customer/store
    都会返回合法 payload（冷启动时返回热门餐品），空结果本身会被缓存。"""
    local_key = _local_reco_key(customer_id, store_id, scene, bucket, fingerprint)
    local_hit = _get_local_reco(local_key)
    if local_hit is not None:
        return local_hit
    try:
        r = await get_redis()
        raw = await r.get(reco_key(customer_id, store_id, scene, bucket, fingerprint))
        if raw:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                payload["cache_hit"] = True
                _set_local_reco(local_key, payload)
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
    body = dict(payload)
    body["cache_hit"] = False
    # 先写本地短缓存，再尝试 Redis；即使 Redis 客户端初始化本身失败也能 single-flight 复用。
    _set_local_reco(
        _local_reco_key(customer_id, store_id, scene, bucket, fingerprint), body
    )
    try:
        r = await get_redis()
        await r.set(
            reco_key(customer_id, store_id, scene, bucket, fingerprint),
            dumps_json(body, compact=True),
            ex=ttl_with_jitter(RECO_TTL_SECONDS),
        )
    except Exception:  # noqa: BLE001
        logger.debug("推荐缓存写入失败，忽略", exc_info=True)



async def set_cached_reco_if_owner(
    customer_id: int | None,
    store_id: int,
    scene: str,
    bucket: str,
    payload: dict[str, Any],
    fingerprint: str,
    token: str,
) -> bool:
    """仅允许仍持有推荐重建租约的 worker 发布 Redis/本地缓存。"""
    if not token:
        return False
    body = dict(payload)
    body["cache_hit"] = False
    try:
        r = await get_redis()
        raw = dumps_json(body, compact=True)
        script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
        end
        return false
        """
        result = await r.eval(
            script,
            2,
            reco_rebuild_lock_key(customer_id, store_id, scene, bucket, fingerprint),
            reco_key(customer_id, store_id, scene, bucket, fingerprint),
            token,
            raw,
            ttl_with_jitter(RECO_TTL_SECONDS),
        )
        if not result:
            return False
        _set_local_reco(
            _local_reco_key(customer_id, store_id, scene, bucket, fingerprint), body
        )
        return True
    except Exception:
        logger.warning("推荐缓存租约校验发布失败(store=%s)", store_id, exc_info=True)
        return False


async def invalidate_customer(customer_id: int) -> None:
    """失效某顾客全部场景/桶位的推荐结果。延迟双删防止并发写窗口旧值残留。"""
    if not customer_id:
        return
    _invalidate_local_reco_customer(customer_id)
    pattern = reco_customer_pattern(customer_id)
    try:
        r = await get_redis()
        # SCAN 使用游标迭代；分批删除，避免某顾客积累大量 cart fingerprint 时
        # 把所有匹配 Key 一次性装入 Python 列表造成额外内存峰值。
        batch = []
        async for k in r.scan_iter(match=pattern, count=200):
            batch.append(k)
            if len(batch) >= 200:
                await delete_with_retry(r, *batch)
                batch.clear()
        if batch:
            await delete_with_retry(r, *batch)
        # 延迟双删：防止删后又有并发请求写入旧推荐结果
        spawn_bg(_delayed_invalidate(pattern))
    except Exception:  # noqa: BLE001
        logger.debug("推荐缓存失效失败(customer=%s)，等待 TTL", customer_id, exc_info=True)


async def _delayed_invalidate(pattern: str) -> None:
    """延迟再删一次，防止并发写窗口中旧值残留。"""
    try:
        await asyncio.sleep(0.1)
        r = await get_redis()
        batch = []
        async for k in r.scan_iter(match=pattern, count=200):
            batch.append(k)
            if len(batch) >= 200:
                await r.delete(*batch)
                batch.clear()
        if batch:
            await r.delete(*batch)
    except Exception:
        pass


async def try_acquire_reco_lock(
    customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str = ""
) -> str | None:
    """缓存击穿保护：返回唯一持有者 token；失败/Redis 不可用返回 None。"""
    try:
        r = await get_redis()
        token = uuid.uuid4().hex
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
    timeout: float = 2.0,
) -> dict[str, Any] | None:
    """未抢到锁时有界轮询缓存，避免固定等待后多个 worker 同时重算。"""
    deadline = asyncio.get_running_loop().time() + max(0.0, float(timeout))
    while True:
        cached = await get_cached_reco(customer_id, store_id, scene, bucket, fingerprint)
        if cached is not None:
            return cached
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(0.10 + (time.monotonic_ns() % 100_000_000) / 1_000_000_000, remaining))

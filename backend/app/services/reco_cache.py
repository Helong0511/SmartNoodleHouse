"""推荐结果 Redis 缓存层（Task 8）。

- 仅缓存接口响应 payload（瘦身字段后的 dict），TTL ≤ 120s；
- Redis 不可用时仅匿名推荐可使用短 TTL 本地缓存；顾客画像结果必须经过共享 Redis 代际校验；
- 顾客画像更新在数据库提交后推进共享代际，旧计算结果不能复活；
- 缓存击穿：SET NX EX 互斥锁，避免并发重建压垮 DB；
- 缓存雪崩：TTL 加 ±20% 随机偏移；
- 旧代际缓存依靠短 TTL 自然回收，不执行高成本 SCAN 双删。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
import time

from sqlalchemy import event

from app.core.async_utils import spawn_bg
from collections import OrderedDict
from threading import RLock
from typing import Any

from app.core.cache_keys import (
    RECO_TTL_SECONDS,
    reco_generation_key,
    reco_key,
    reco_rebuild_lock_key,
    ttl_with_jitter,
)
from app.core.redis_client import (
    get_redis,
    release_owned_lock,
    start_redis_lock_heartbeat,
    stop_redis_lock_heartbeat,
)
from app.core.response import dumps_json

logger = logging.getLogger(__name__)

_REBUILD_LOCK_TTL = 30  # 短租约由 owner-token 心跳续期
_REBUILD_HEARTBEATS: dict[str, asyncio.Task] = {}

# Redis 故障时仅为匿名推荐保留短 TTL 进程内兜底；顾客级推荐不缓存于本地，避免跨 Worker 授权撤回后继续返回画像结果。
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



async def get_reco_generation(customer_id: int | None) -> int | None:
    """读取顾客推荐缓存代际；Redis 不可用返回 None，调用方必须 fail-closed。"""
    if customer_id is None:
        return 0
    try:
        r = await get_redis()
        return max(0, int(await r.get(reco_generation_key(customer_id)) or 0))
    except Exception:
        logger.warning("读取推荐缓存代际失败(customer=%s)，禁止读取个性化缓存", customer_id, exc_info=True)
        return None


async def get_cached_reco(
    customer_id: int | None, store_id: int, scene: str, bucket: str,
    fingerprint: str = "", *, generation: int | None = None,
) -> dict[str, Any] | None:
    """按顾客代际读取缓存；跨 Worker 的顾客级推荐不使用本地短缓存。"""
    gen = await get_reco_generation(customer_id) if generation is None else max(0, int(generation))
    # 个性化结果的版本未知时不能猜测为 0，否则 Redis 短暂故障恢复后可能命中旧代际缓存。
    if customer_id is not None and gen is None:
        return None
    local_key = _local_reco_key(customer_id, store_id, scene, bucket, fingerprint)
    if customer_id is None:
        local_hit = _get_local_reco(local_key)
        if local_hit is not None:
            return local_hit
    try:
        r = await get_redis()
        raw = await r.get(reco_key(customer_id, store_id, scene, bucket, fingerprint, gen))
        if raw:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                payload["cache_hit"] = True
                if customer_id is None:
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
    # 无 owner token 的便捷写接口只允许匿名结果；顾客级缓存必须走 generation + owner Lua fencing。
    if customer_id is not None:
        logger.warning("拒绝无租约写入顾客级推荐缓存(customer=%s)", customer_id)
        return
    body = dict(payload)
    body["cache_hit"] = False
    _set_local_reco(
        _local_reco_key(None, store_id, scene, bucket, fingerprint), body
    )
    try:
        r = await get_redis()
        await r.set(
            reco_key(None, store_id, scene, bucket, fingerprint, 0),
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
    *,
    expected_generation: int = 0,
) -> bool:
    """仅允许仍持有租约且顾客代际未变化的 worker 发布缓存。"""
    if not token:
        return False
    body = dict(payload)
    body["cache_hit"] = False
    try:
        r = await get_redis()
        raw = dumps_json(body, compact=True)
        script = """
        if redis.call('GET', KEYS[1]) == ARGV[1]
           and (redis.call('GET', KEYS[3]) or '0') == ARGV[4] then
            return redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
        end
        return false
        """
        result = await r.eval(
            script,
            3,
            reco_rebuild_lock_key(customer_id, store_id, scene, bucket, fingerprint, expected_generation),
            reco_key(customer_id, store_id, scene, bucket, fingerprint, expected_generation),
            reco_generation_key(customer_id),
            token,
            raw,
            ttl_with_jitter(RECO_TTL_SECONDS),
            str(max(0, int(expected_generation))),
        )
        if not result:
            return False
        if customer_id is None:
            _set_local_reco(
                _local_reco_key(customer_id, store_id, scene, bucket, fingerprint), body
            )
        return True
    except Exception:
        logger.warning("推荐缓存租约校验发布失败(store=%s)", store_id, exc_info=True)
        return False


def defer_invalidate_customer(db, customer_id: int) -> None:
    """把顾客缓存代际失效挂到当前 DB session 的 after_commit，避免提交前刷新造成旧快照复活。"""
    if not customer_id:
        return
    session = getattr(db, "sync_session", db)
    info = session.info
    pending = info.setdefault("_reco_cache_invalidation_ids", set())
    pending.add(int(customer_id))
    if info.get("_reco_cache_invalidation_listener"):
        return
    info["_reco_cache_invalidation_listener"] = True

    def _after_commit(sync_session) -> None:
        # FeedbackService 会在 SAVEPOINT 内学习画像；嵌套事务提交不是业务事务提交。
        # 只有最外层事务结束提交后才推进代际，防止外层回滚却提前失效/发布新版本。
        if sync_session.in_transaction() or sync_session.in_nested_transaction():
            return
        customer_ids = tuple(sync_session.info.pop("_reco_cache_invalidation_ids", set()))
        for cid in customer_ids:
            spawn_bg(invalidate_customer(cid))

    def _after_rollback(sync_session) -> None:
        # 仅外层事务回滚时清理待失效集合；SAVEPOINT 回滚不应抹掉外层已排队的失效。
        if not sync_session.in_transaction() and not sync_session.in_nested_transaction():
            sync_session.info.pop("_reco_cache_invalidation_ids", None)

    event.listen(session, "after_commit", _after_commit)
    event.listen(session, "after_rollback", _after_rollback)


async def invalidate_customer(customer_id: int) -> None:
    """原子推进顾客缓存代际；旧的慢速重建任务无法把失效前快照写回。"""
    if not customer_id:
        return
    _invalidate_local_reco_customer(customer_id)
    try:
        r = await get_redis()
        key = reco_generation_key(customer_id)
        # INCR 与 EXPIRE 必须原子执行；否则进程若在两条命令之间崩溃，会留下永久代际键。
        await r.eval(
            """
            local generation = redis.call('INCR', KEYS[1])
            redis.call('EXPIRE', KEYS[1], ARGV[1])
            return generation
            """,
            1,
            key,
            30 * 24 * 3600,
        )
        # 旧代际键使用短 TTL 自然回收；无需 SCAN/双删，避免画像高频更新放大 Redis 负载。
    except Exception:  # noqa: BLE001
        logger.warning("推荐缓存代际失效失败(customer=%s)，旧缓存等待 TTL", customer_id, exc_info=True)


async def try_acquire_reco_lock(
    customer_id: int | None, store_id: int, scene: str, bucket: str,
    fingerprint: str = "", *, generation: int = 0,
) -> str | None:
    """缓存击穿保护：返回唯一持有者 token；失败/Redis 不可用返回 None。"""
    try:
        r = await get_redis()
        token = uuid.uuid4().hex
        key = reco_rebuild_lock_key(customer_id, store_id, scene, bucket, fingerprint, generation)
        acquired = await r.set(key, token, nx=True, ex=_REBUILD_LOCK_TTL)
        if not acquired:
            return None
        heartbeat = start_redis_lock_heartbeat(
            r, key, token, ttl=_REBUILD_LOCK_TTL,
            label=f"reco:{store_id}:{scene}",
        )
        _REBUILD_HEARTBEATS[token] = heartbeat
        heartbeat.add_done_callback(
            lambda _task, owner=token: _REBUILD_HEARTBEATS.pop(owner, None)
        )
        return token
    except Exception:
        return None


async def release_reco_lock(
    customer_id: int | None, store_id: int, scene: str, bucket: str,
    fingerprint: str = "", token: str | None = None, *, generation: int = 0,
) -> None:
    """只释放当前调用方持有的锁，避免误删后续持有者的锁。"""
    if not token:
        return
    heartbeat = _REBUILD_HEARTBEATS.pop(token, None)
    await stop_redis_lock_heartbeat(heartbeat)
    try:
        r = await get_redis()
        await release_owned_lock(
            r, reco_rebuild_lock_key(customer_id, store_id, scene, bucket, fingerprint, generation), token
        )
    except Exception:
        pass


async def wait_for_reco(
    customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str = "",
    timeout: float = 2.0, *, generation: int | None = None,
) -> dict[str, Any] | None:
    """未抢到锁时有界轮询缓存，避免固定等待后多个 worker 同时重算。"""
    deadline = asyncio.get_running_loop().time() + max(0.0, float(timeout))
    while True:
        cached = await get_cached_reco(
            customer_id, store_id, scene, bucket, fingerprint, generation=generation
        )
        if cached is not None:
            return cached
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(0.10 + (time.monotonic_ns() % 100_000_000) / 1_000_000_000, remaining))

"""Webhook / 支付回调防重放（Redis 软加速 + MySQL 事实源）。

委托 ``domains.messaging.idempotency``；保留本模块 API 以兼容既有 payment_service 调用。
"""
from __future__ import annotations

import hashlib
import logging

from app.core.redis_client import try_get_redis
from app.domains.messaging import idempotency as _idem

logger = logging.getLogger(__name__)

REDIS_NS = "snh:webhook:replay:"
DEFAULT_TTL = 86400 * 2  # 48h


async def claim_once(channel: str, receipt_key: str, ttl: int = DEFAULT_TTL) -> bool:
    """
    Redis 原子认领一次回调。返回 True 表示本进程可继续处理；False 表示近期已有人认领。

    降级策略：Redis 失败时返回 True（放行）。
    唯一事实源是 DB 表 PaymentCallbackReceipt 的 UNIQUE(provider, receipt_key)；
    Redis 仅作加速去重，不可替代 DB 约束。处理失败时务必调用 release_claim，
    避免永久占 key 导致渠道重试无法入账。
    """
    key = f"{REDIS_NS}{channel}:{receipt_key}"
    r = await try_get_redis()
    if r is None:
        logger.error("Redis replay claim 不可用，降级放行（DB UNIQUE 兜底）")
        return True
    try:
        ok = await r.set(key, "1", nx=True, ex=ttl)
        return bool(ok)
    except Exception as e:
        logger.error("Redis replay claim 失败，降级放行（DB UNIQUE 兜底）: %s", e)
        return True


async def release_claim(channel: str, receipt_key: str) -> None:
    """处理失败时删除 Redis key，允许渠道/定时任务重试。"""
    key = f"{REDIS_NS}{channel}:{receipt_key}"
    r = await try_get_redis()
    if r is None:
        return
    try:
        await r.delete(key)
    except Exception as e:
        logger.warning("Redis replay release 失败 channel=%s: %s", channel, e)


def make_receipt_key(*parts: str) -> str:
    raw = "|".join(p or "" for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


# 通用幂等（打印 / outbox 旁路等）直接复用
try_acquire_message = _idem.try_acquire
release_message = _idem.release
is_message_processed = _idem.is_processed

"""Webhook / 支付回调防重放（Redis 软加速 + MySQL 事实源）。

委托 ``domains.messaging.idempotency``；保留本模块 API 以兼容既有 payment_service 调用。
"""
from __future__ import annotations

import hashlib
import logging
import secrets

from app.core.cache_keys import WEBHOOK_REPLAY_PREFIX
from app.core.redis_client import try_get_redis
from app.domains.messaging import idempotency as _idem

logger = logging.getLogger(__name__)

REDIS_NS = WEBHOOK_REPLAY_PREFIX
DEFAULT_TTL = 86400 * 2  # 48h

# 只允许当前认领者释放锁。旧请求超时后，即使新请求重新认领同一 key，
# 旧请求的 finally/异常清理也不能删除新请求的锁。
_RELEASE_IF_OWNER_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


async def claim_once(channel: str, receipt_key: str, ttl: int = DEFAULT_TTL) -> str | None:
    """
    Redis 原子认领一次回调，返回本次唯一 owner token；None 表示已有请求认领。

    Redis 不可用时 fail-open，仍返回本次 token（释放时 Lua 比较失败即安全 no-op）。
    唯一事实源是 DB 表 PaymentCallbackReceipt 的 UNIQUE(provider, receipt_key)；
    Redis 仅作加速去重，不可替代 DB 约束。
    """
    key = f"{REDIS_NS}{channel}:{receipt_key}"
    owner_token = secrets.token_urlsafe(32)
    r = await try_get_redis()
    if r is None:
        logger.error("Redis replay claim 不可用，降级放行（DB UNIQUE 兜底）")
        return owner_token
    try:
        ok = await r.set(key, owner_token, nx=True, ex=max(1, int(ttl)))
        return owner_token if ok else None
    except Exception as e:
        logger.error("Redis replay claim 失败，降级放行（DB UNIQUE 兜底）: %s", e)
        return owner_token


async def release_claim(channel: str, receipt_key: str, owner_token: str | None) -> None:
    """仅 owner_token 仍匹配时释放 Redis key；不允许无 token 的无条件 DEL。"""
    if not owner_token:
        return
    key = f"{REDIS_NS}{channel}:{receipt_key}"
    r = await try_get_redis()
    if r is None:
        return
    try:
        await r.eval(_RELEASE_IF_OWNER_LUA, 1, key, owner_token)
    except Exception as e:
        logger.warning("Redis replay release 失败 channel=%s: %s", channel, e)


def make_receipt_key(*parts: str) -> str:
    raw = "|".join(p or "" for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


# 通用幂等（打印 / outbox 旁路等）直接复用
try_acquire_message = _idem.try_acquire
release_message = _idem.release
is_message_processed = _idem.is_processed

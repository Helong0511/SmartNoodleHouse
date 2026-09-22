"""消息消费幂等：Redis 加速 + MySQL 事实源。

典型用法（支付回调 / 打印 / outbox 消费）：
1. ``acquired = await try_acquire(db, scope, message_id)``
2. 若 False → 重复消息，直接丢弃
3. 处理业务
4. 失败时 ``await release(db, scope, message_id)`` 允许重试
5. 成功则保留记录（Redis TTL 过期后仍可由 MySQL 拦截）
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import try_get_redis
from app.models import ProcessedMessage

logger = logging.getLogger(__name__)

REDIS_NS = "snh:msg:done:"
DEFAULT_TTL = 86400 * 2  # 48h


def _redis_key(scope: str, message_id: str) -> str:
    return f"{REDIS_NS}{scope}:{message_id}"


async def _redis_seen(scope: str, message_id: str) -> bool | None:
    """None=Redis 不可用；True=已见过；False=未见。"""
    r = await try_get_redis()
    if r is None:
        return None
    try:
        return bool(await r.exists(_redis_key(scope, message_id)))
    except Exception as e:
        logger.warning("Redis 幂等查询失败 scope=%s: %s", scope, e)
        return None


async def _redis_mark(scope: str, message_id: str, ttl: int) -> None:
    r = await try_get_redis()
    if r is None:
        return
    try:
        await r.set(_redis_key(scope, message_id), "1", ex=max(60, int(ttl)))
    except Exception as e:
        logger.warning("Redis 幂等写入失败 scope=%s: %s", scope, e)


async def _redis_clear(scope: str, message_id: str) -> None:
    r = await try_get_redis()
    if r is None:
        return
    try:
        await r.delete(_redis_key(scope, message_id))
    except Exception as e:
        logger.warning("Redis 幂等清除失败 scope=%s: %s", scope, e)


async def try_acquire(
    db: AsyncSession,
    *,
    scope: str,
    message_id: str,
    ttl_seconds: int = DEFAULT_TTL,
    meta: dict[str, Any] | None = None,
) -> bool:
    """认领一条消息。True=首次（应处理）；False=重复（应丢弃）。

    MySQL ``processed_messages(scope, message_id)`` UNIQUE 是唯一事实源。
    Redis 仅加速「已处理」判断；宕机时直接走 DB，业务仍可用。
    """
    scope = (scope or "").strip()[:64]
    message_id = (message_id or "").strip()[:128]
    if not scope or not message_id:
        raise ValueError("scope/message_id 不能为空")

    seen = await _redis_seen(scope, message_id)
    if seen is True:
        return False

    # 先查库：避免 UNIQUE 冲突把共享业务会话打进 PendingRollback（尤以 SQLite 明显）
    existed = await db.scalar(
        select(ProcessedMessage.id).where(
            ProcessedMessage.scope == scope,
            ProcessedMessage.message_id == message_id,
        )
    )
    if existed:
        await _redis_mark(scope, message_id, ttl_seconds)
        return False

    meta_json = None
    if meta:
        import json

        meta_json = json.dumps(meta, ensure_ascii=False, default=str)[:500]

    row = ProcessedMessage(scope=scope, message_id=message_id, meta_json=meta_json)
    db.add(row)
    try:
        # SAVEPOINT：并发下仍可能撞 UNIQUE，隔离冲突不回滚外层业务事务
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        try:
            db.expunge(row)
        except Exception:
            pass
        await _redis_mark(scope, message_id, ttl_seconds)
        return False

    await _redis_mark(scope, message_id, ttl_seconds)
    return True


async def release(
    db: AsyncSession,
    *,
    scope: str,
    message_id: str,
) -> None:
    """处理失败时释放认领，允许渠道/队列重试。"""
    scope = (scope or "").strip()[:64]
    message_id = (message_id or "").strip()[:128]
    await db.execute(
        delete(ProcessedMessage).where(
            ProcessedMessage.scope == scope,
            ProcessedMessage.message_id == message_id,
        )
    )
    await _redis_clear(scope, message_id)


async def is_processed(db: AsyncSession, *, scope: str, message_id: str) -> bool:
    seen = await _redis_seen(scope, message_id)
    if seen is True:
        return True
    row = await db.scalar(
        select(ProcessedMessage.id).where(
            ProcessedMessage.scope == scope,
            ProcessedMessage.message_id == message_id,
        )
    )
    if row:
        await _redis_mark(scope, message_id, DEFAULT_TTL)
        return True
    return False

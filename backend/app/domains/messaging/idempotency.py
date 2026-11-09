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

from app.core.cache_keys import MSG_DONE_PREFIX
from app.core.redis_client import try_get_redis
from app.models import ProcessedMessage

logger = logging.getLogger(__name__)

REDIS_NS = MSG_DONE_PREFIX
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
    mark_redis: bool = True,
) -> bool:
    """认领一条消息。True=首次（应处理）；False=重复（应丢弃）。

    MySQL ``processed_messages(scope, message_id)`` UNIQUE 是唯一事实源。
    Redis 仅加速「已处理」判断；宕机时直接走 DB，业务仍可用。

    mark_redis: 默认 True。若仍在未提交的外层事务内，应传 False，
    并在 commit 成功后调用 ``mark_processed_redis``，避免回滚后 Redis 脏标记。
    """
    scope = (scope or "").strip()[:64]
    message_id = (message_id or "").strip()[:128]
    if not scope or not message_id:
        raise ValueError("scope/message_id 不能为空")

    seen = await _redis_seen(scope, message_id)
    if seen is True:
        existed = await db.scalar(
            select(ProcessedMessage.id).where(
                ProcessedMessage.scope == scope,
                ProcessedMessage.message_id == message_id,
            )
        )
        if existed:
            return False
        # Redis 有、DB 无：脏缓存（外层事务曾回滚），清除后继续认领
        await _redis_clear(scope, message_id)

    # 先查库：避免 UNIQUE 冲突把共享业务会话打进 PendingRollback（尤以 SQLite 明显）
    existed = await db.scalar(
        select(ProcessedMessage.id).where(
            ProcessedMessage.scope == scope,
            ProcessedMessage.message_id == message_id,
        )
    )
    if existed:
        if mark_redis:
            await _redis_mark(scope, message_id, ttl_seconds)
        return False

    meta_json = None
    if meta:
        from app.core.response import dumps_json

        meta_json = dumps_json(meta)[:500]

    row = ProcessedMessage(scope=scope, message_id=message_id, meta_json=meta_json)
    try:
        # 必须先建立 SAVEPOINT，再 add/flush。
        # SQLAlchemy begin_nested() 会先 flush 当前 Session 的待处理对象；
        # 若在 begin_nested() 前 db.add(row)，唯一键冲突可能发生在 SAVEPOINT 外，
        # 进而把调用方整个业务事务置为 PendingRollback。
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        try:
            db.expunge(row)
        except Exception:
            pass
        if mark_redis:
            await _redis_mark(scope, message_id, ttl_seconds)
        return False

    if mark_redis:
        await _redis_mark(scope, message_id, ttl_seconds)
    return True


async def mark_processed_redis(
    *,
    scope: str,
    message_id: str,
    ttl_seconds: int = DEFAULT_TTL,
) -> None:
    """外层事务 commit 成功后回填 Redis 加速键。"""
    scope = (scope or "").strip()[:64]
    message_id = (message_id or "").strip()[:128]
    if scope and message_id:
        await _redis_mark(scope, message_id, ttl_seconds)


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
    """是否已处理成功。以 MySQL processed_messages 为准，禁止仅凭 Redis 判定。

    Redis 只作加速缓存：仅在 DB 已有记录时回填。若先信 Redis，会出现
    「Redis 已标记但外层事务回滚」→ 重试误跳过业务（如券一直停在 RESERVED）。
    """
    scope = (scope or "").strip()[:64]
    message_id = (message_id or "").strip()[:128]
    if not scope or not message_id:
        return False
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

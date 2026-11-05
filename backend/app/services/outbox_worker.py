"""DB 事务型 outbox worker：原子 claim、stale 回收、指数退避、handler 注册表。

用于支付成功副作用（积分/券/口味）与退款补偿，替代易丢的 Redis list。
handler 必须自身幂等（按 aggregate_id 去重），outbox 表 UNIQUE 仅防止事件本身重复入队。
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.response import now_local
from app.models import OutboxEvent
from app.services.worker_common import backoff_seconds, claim_lease_guard, claim_token, worker_id

logger = logging.getLogger(__name__)

# 最大重试次数；超过后保持 FAILED，不再自动 claim
MAX_ATTEMPTS = 8
# 指数退避（秒）：2^attempt，上限 10 分钟
BACKOFF_CAP_SEC = 600
# stale PROCESSING 回收阈值：locked_at 超过此秒数视为崩溃残留
STALE_PROCESSING_SEC = 300

# handler 注册表：(aggregate_type, event_kind) -> async (db, row) -> None
OutboxHandler = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]
OUTBOX_HANDLERS: dict[tuple[str, str], OutboxHandler] = {}


def register_outbox_handler(agg_type: str, kind: str) -> Callable[[OutboxHandler], OutboxHandler]:
    """装饰器：注册 outbox handler。handler 必须幂等。"""

    def _decorator(fn: OutboxHandler) -> OutboxHandler:
        OUTBOX_HANDLERS[(agg_type, kind)] = fn
        return fn

    return _decorator


def _backoff_seconds(attempt_count: int) -> int:
    """指数退避：2^attempt 秒，封顶 10 分钟。"""
    return backoff_seconds(attempt_count, cap=BACKOFF_CAP_SEC)


async def enqueue_outbox(
    db: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: int,
    event_kind: str,
    payload: dict[str, Any] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> tuple[OutboxEvent, bool]:
    """同事务写入 outbox 事件。返回 (row, is_new)。

    幂等：(aggregate_type, aggregate_id, event_kind) 冲突时返回已有行且 is_new=False。
    必须在业务事务内调用，commit 由调用方负责。
    """
    from app.core.response import dumps_json

    payload_str = dumps_json(payload) if payload else None
    row = OutboxEvent(
        aggregate_type=aggregate_type,
        aggregate_id=int(aggregate_id),
        event_kind=event_kind,
        payload=payload_str,
        status="PENDING",
        max_attempts=max_attempts,
    )
    try:
        # 使用 SAVEPOINT 隔离插入失败：UNIQUE 冲突仅回滚 savepoint，
        # 不影响调用方外部事务中的已有修改。
        async with db.begin_nested():
            db.add(row)
            await db.flush()
        return row, True
    except IntegrityError:
        existing = (
            await db.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == aggregate_type,
                    OutboxEvent.aggregate_id == int(aggregate_id),
                    OutboxEvent.event_kind == event_kind,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return existing, False
        raise


async def reclaim_stale_processing(db: AsyncSession, *, stale_after: int = STALE_PROCESSING_SEC) -> int:
    """回收崩溃残留的 PROCESSING 行：locked_at < now - stale_after → 复位 PENDING。

    复位时不清零 attempt_count（避免无限重试），仅释放锁、保留退避。
    """
    cutoff = now_local() - timedelta(seconds=stale_after)
    stmt = (
        update(OutboxEvent)
        .where(
            OutboxEvent.status == "PROCESSING",
            or_(OutboxEvent.locked_at.is_(None), OutboxEvent.locked_at < cutoff),
        )
        .values(status="PENDING", locked_at=None, locked_by=None)
    )
    result = await db.execute(stmt)
    await db.commit()
    count = int(result.rowcount or 0)
    if count:
        logger.warning("Reclaimed %s stale PROCESSING outbox events", count)
    return count


async def claim_outbox_item(
    db: AsyncSession, outbox_id: int, *, worker: str | None = None
) -> OutboxEvent | None:
    """原子 claim：仅当 status=PENDING（或到点可重试的 FAILED）时 UPDATE 为 PROCESSING。

    双 worker 并发时只有一行能更新成功，另一行 rowcount=0。
    """
    wid = claim_token(worker)
    now = now_local()
    stmt = (
        update(OutboxEvent)
        .where(
            OutboxEvent.id == outbox_id,
            or_(
                OutboxEvent.status == "PENDING",
                and_(
                    OutboxEvent.status == "FAILED",
                    OutboxEvent.attempt_count < OutboxEvent.max_attempts,
                    or_(
                        OutboxEvent.next_retry_at.is_(None),
                        OutboxEvent.next_retry_at <= now,
                    ),
                ),
            ),
        )
        .values(
            status="PROCESSING",
            locked_at=now,
            locked_by=wid,
            attempt_count=OutboxEvent.attempt_count + 1,
        )
    )
    result = await db.execute(stmt)
    await db.commit()
    if not result.rowcount:
        return None
    return await db.get(OutboxEvent, outbox_id)


async def process_outbox_item(outbox_id: int) -> None:
    """claim 单条 outbox 事件并分发给已注册 handler。

    claim 释放使用原子 UPDATE WHERE locked_by = wid：处理期间若行被
    reclaim_stale_outbox 复位并被另一 worker 重新 claim，原 worker 的
    UPDATE 命中 0 行，不会覆盖新 owner 的锁。
    """
    async with AsyncSessionLocal() as db:
        row = await claim_outbox_item(db, outbox_id)
        if not row:
            # 已被其他 worker claim，或 DONE / 未到重试时间
            return
        # claim 后 row.locked_by 即本 worker 的 owner token
        wid = (row.locked_by or "")[:64]
        # 库存镜像 Outbox 行会被反复复用，必须由 sync_version 驱动重新投递；
        # 不能使用一次性 processed_messages/Redis 幂等键，否则首次成功后所有后续
        # 库存变化都会被旧幂等记录跳过。
        is_stock_mirror = (
            row.aggregate_type in ("delivery_stock", "delivery_stock_base")
            and row.event_kind == "mirror"
        )
        # 普通业务事件仍使用持久幂等预检，防止重复发券、积分等副作用。
        if not is_stock_mirror:
            try:
                from app.domains.messaging.idempotency import is_processed

                msg_id = f"{row.aggregate_type}:{row.aggregate_id}:{row.event_kind}"
                if await is_processed(db, scope="outbox", message_id=msg_id):
                    done_result = await db.execute(
                        update(OutboxEvent)
                        .where(
                            OutboxEvent.id == outbox_id,
                            OutboxEvent.locked_by == wid,
                        )
                        .values(
                            status="DONE",
                            processed_at=now_local(),
                            last_error=None,
                            next_retry_at=None,
                            locked_at=None,
                            locked_by=None,
                        )
                    )
                    from app.services.worker_common import rollback_if_claim_lost

                    if await rollback_if_claim_lost(
                        db, done_result, model=OutboxEvent, row_id=outbox_id
                    ):
                        return
                    await db.commit()
                    return
            except Exception as e:
                # 无法确认消息是否已处理时必须 fail-closed；继续 handler 可能重复发券/记积分。
                logger.exception("outbox 幂等预检失败 id=%s，停止处理并安排重试", outbox_id)
                await db.rollback()
                await _mark_failed(db, row, f"idempotency_precheck_failed: {e}", wid=wid)
                return

        handler = OUTBOX_HANDLERS.get((row.aggregate_type, row.event_kind))
        if handler is None:
            logger.error(
                "Outbox handler not registered: %s/%s id=%s",
                row.aggregate_type,
                row.event_kind,
                outbox_id,
            )
            await _mark_failed(db, row, "handler_not_registered", wid=wid)
            return
        try:
            json.loads(row.payload) if row.payload else {}
        except (TypeError, ValueError):
            # payload 损坏无法解析，标记失败不再执行 handler，避免空 payload 继续处理
            await _mark_failed(db, row, "payload_invalid_json", wid=wid)
            return
        try:
            async with claim_lease_guard(OutboxEvent, outbox_id, wid):
                await handler(db, row)
                # 库存镜像是可合并事件：处理期间若库存再次变化，不能标记 DONE，
                # 必须在当前 HTTP 请求全部结束后重新排队推送最新绝对库存。
                is_stock_mirror = (
                    row.aggregate_type in ("delivery_stock", "delivery_stock_base")
                    and row.event_kind == "mirror"
                )
                expected_sync_version = int(getattr(row, "sync_version", 0) or 0)
                done_conditions = [
                    OutboxEvent.id == outbox_id,
                    OutboxEvent.locked_by == wid,
                ]
                if is_stock_mirror:
                    done_conditions.append(OutboxEvent.sync_version == expected_sync_version)
                done_stmt = (
                    update(OutboxEvent)
                    .where(*done_conditions)
                    .values(
                        status="DONE",
                        processed_at=now_local(),
                        last_error=None,
                        next_retry_at=None,
                        locked_at=None,
                        locked_by=None,
                    )
                )
                done_result = await db.execute(done_stmt)

                if is_stock_mirror and int(done_result.rowcount or 0) != 1:
                    # rowcount=0 可能是库存版本已变化，也可能是 claim 已丢失。
                    # 先回滚 handler 的本地事务，再用当前读确认 owner；只有仍持有
                    # 原 claim 时才将事件 PENDING，绝不覆盖新 Worker 的状态。
                    await db.rollback()
                    fresh = (
                        await db.execute(
                            select(OutboxEvent)
                            .where(
                                OutboxEvent.id == outbox_id,
                                OutboxEvent.status == "PROCESSING",
                                OutboxEvent.locked_by == wid,
                            )
                            .execution_options(populate_existing=True)
                        )
                    ).scalar_one_or_none()
                    if fresh is not None:
                        requeue_result = await db.execute(
                            update(OutboxEvent)
                            .where(
                                OutboxEvent.id == outbox_id,
                                OutboxEvent.status == "PROCESSING",
                                OutboxEvent.locked_by == wid,
                            )
                            .values(
                                status="PENDING",
                                attempt_count=0,
                                next_retry_at=None,
                                locked_at=None,
                                locked_by=None,
                                last_error=None,
                            )
                        )
                        if int(requeue_result.rowcount or 0) == 1:
                            await db.commit()
                        else:
                            await db.rollback()
                    return

                from app.services.worker_common import rollback_if_claim_lost

                if await rollback_if_claim_lost(
                    db, done_result, model=OutboxEvent, row_id=outbox_id
                ):
                    return
                # 普通一次性业务事件需要持久幂等；库存镜像是可重复触发的状态收敛信号，
                # 每次由 sync_version 标识新一代，不得写入永久 processed_messages 键。
                if not is_stock_mirror:
                    idem_msg = f"{row.aggregate_type}:{row.aggregate_id}:{row.event_kind}"
                    try:
                        from app.domains.messaging.idempotency import try_acquire

                        idem_acquired = await try_acquire(
                            db,
                            scope="outbox",
                            message_id=idem_msg,
                            ttl_seconds=86400 * 7,
                            mark_redis=False,
                        )
                        if not idem_acquired:
                            await db.rollback()
                            await _mark_failed(
                                db, row, "idempotency_already_exists_after_handler", wid=wid
                            )
                            return
                    except Exception as e:
                        await db.rollback()
                        await _mark_failed(
                            db, row, f"idempotency_write_failed: {e}", wid=wid
                        )
                        return
                await db.commit()
            if not is_stock_mirror:
                try:
                    from app.domains.messaging.idempotency import mark_processed_redis

                    await mark_processed_redis(
                        scope="outbox", message_id=idem_msg, ttl_seconds=86400 * 7
                    )
                except Exception:
                    logger.warning("outbox 幂等 Redis 回填失败 id=%s", outbox_id)
        except Exception as e:
            logger.exception("Outbox handler failed id=%s agg=%s/%s: %s", outbox_id, row.aggregate_type, row.event_kind, e)
            # handler 异常后会话可能处于失败态，先回滚再用同一会话标记失败
            await db.rollback()
            await _mark_failed(db, row, str(e), wid=wid)


async def _mark_failed(db: AsyncSession, row: OutboxEvent, err: str, *, wid: str = "") -> None:
    """handler 异常时标记 FAILED 并安排退避；超过 max_attempts 不再自动重试。

    原子 UPDATE WHERE locked_by = wid：仅当锁仍归原 worker 时才标记失败，
    避免 reclaim 后新 owner 的锁被误覆盖。
    """
    # 重新加载以获取 attempt_count / max_attempts
    fresh = await db.get(OutboxEvent, row.id)
    if not fresh or fresh.status == "DONE":
        return
    attempts = int(fresh.attempt_count or 1)
    max_att = int(fresh.max_attempts or MAX_ATTEMPTS)
    retry_at = (
        now_local() + timedelta(seconds=_backoff_seconds(attempts))
        if attempts < max_att
        else None
    )
    fail_stmt = (
        update(OutboxEvent)
        .where(
            OutboxEvent.id == row.id,
            OutboxEvent.locked_by == wid,
        )
        .values(
            status="FAILED",
            last_error=err[:500],
            locked_at=None,
            locked_by=None,
            next_retry_at=retry_at,
        )
    )
    fail_result = await db.execute(fail_stmt)
    await db.commit()
    if not fail_result.rowcount:
        logger.warning(
            "Outbox 行在失败标记期间被 reclaim 释放 id=%s", row.id
        )


async def process_pending_outbox(limit: int = 30) -> int:
    """扫描可消费 outbox 事件并逐条 claim；返回真正 claim 成功的数量。

    与 webhook_inbox 不同：这里在 claim 成功后才计 done，避免把"调用次数"误当"处理数"。
    """
    # 懒加载触发 handler 注册（paid_side_effects 等），避免循环 import。
    # import 是幂等的，后续调用仅字典查找。
    try:
        import app.services.paid_side_effects  # noqa: F401
    except Exception:
        logger.exception("Failed to import paid_side_effects handlers")
    try:
        import app.services.delivery_sync_handlers  # noqa: F401
    except Exception:
        logger.exception("Failed to import delivery_sync_handlers handlers")
    now = now_local()
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(OutboxEvent.id)
                .where(
                    or_(
                        OutboxEvent.status == "PENDING",
                        and_(
                            OutboxEvent.status == "FAILED",
                            OutboxEvent.attempt_count < OutboxEvent.max_attempts,
                            or_(
                                OutboxEvent.next_retry_at.is_(None),
                                OutboxEvent.next_retry_at <= now,
                            ),
                        ),
                    )
                )
                .order_by(OutboxEvent.created_at)
                .limit(limit)
            )
        ).scalars().all()
        ids = list(rows)
    claimed = 0
    for oid in ids:
        # process_outbox_item 内部原子 claim；通过事后查询行状态确认是否真正 claim 成功
        before_status = await _peek_status(oid)
        await process_outbox_item(oid)
        after_status = await _peek_status(oid)
        # 若状态从 PENDING/FAILED 变为 PROCESSING→DONE/FAILED，说明本次 claim 成功
        if before_status in ("PENDING", "FAILED") and after_status in ("DONE", "FAILED", "PROCESSING"):
            if before_status != after_status or after_status == "DONE":
                claimed += 1
    return claimed


async def _peek_status(outbox_id: int) -> str | None:
    """只读查询行状态，独立短事务，不干扰主流程。"""
    async with AsyncSessionLocal() as db:
        row = await db.get(OutboxEvent, outbox_id)
        return row.status if row else None

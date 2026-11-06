"""DB 事务型 outbox worker：原子 claim、stale 回收、指数退避、handler 注册表。

用于支付成功副作用（积分/券/口味）与退款补偿，替代易丢的 Redis list。
handler 必须自身幂等（按 aggregate_id 去重），outbox 表 UNIQUE 仅防止事件本身重复入队。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.response import now_local
from app.models import OutboxEvent
from app.services.worker_common import backoff_seconds, worker_id

logger = logging.getLogger(__name__)

# 最大重试次数；超过后保持 FAILED，不再自动 claim
MAX_ATTEMPTS = 8
# 指数退避（秒）：2^attempt，上限 10 分钟
BACKOFF_CAP_SEC = 600
# stale PROCESSING 回收阈值：locked_at 超过此秒数视为崩溃残留
STALE_PROCESSING_SEC = 300
# Worker 活跃期间续租；必须显著小于 stale 阈值。
LEASE_HEARTBEAT_INTERVAL_SEC = 60
# DB 连续不可用时必须在 stale reclaim 阈值前停止旧 handler，避免失租后继续发起外部副作用。
LEASE_HEARTBEAT_MAX_FAILURE_SEC = 120
LEASE_HEARTBEAT_DB_TIMEOUT_SEC = 15

# handler 注册表：(aggregate_type, event_kind) -> async (db, row) -> None
OutboxHandler = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]
OUTBOX_HANDLERS: dict[tuple[str, str], OutboxHandler] = {}


class ExternalSideEffectUncertain(RuntimeError):
    """下游可能已执行副作用，但调用方未收到可确认结果；禁止自动重试。"""


def register_outbox_handler(agg_type: str, kind: str) -> Callable[[OutboxHandler], OutboxHandler]:
    """装饰器：注册 outbox handler。handler 必须幂等。"""

    def _decorator(fn: OutboxHandler) -> OutboxHandler:
        OUTBOX_HANDLERS[(agg_type, kind)] = fn
        return fn

    return _decorator


def _backoff_seconds(attempt_count: int) -> int:
    """指数退避：2^attempt 秒，封顶 10 分钟。"""
    return backoff_seconds(attempt_count, cap=BACKOFF_CAP_SEC)


def _resolve_outbox_handler(row: OutboxEvent) -> OutboxHandler | None:
    """支持带唯一后缀的推荐失效与 Webhook 延迟动作，实际执行仍由白名单 handler 负责。"""
    handler = OUTBOX_HANDLERS.get((row.aggregate_type, row.event_kind))
    if handler is None and row.aggregate_type == "reco_cache" and row.event_kind.startswith("invalidate_"):
        handler = OUTBOX_HANDLERS.get(("reco_cache", "invalidate"))
    if handler is None and row.aggregate_type == "webhook_task" and row.event_kind.startswith("deferred_"):
        handler = OUTBOX_HANDLERS.get(("webhook_task", "execute"))
    if handler is None and row.aggregate_type == "delivery_manual" and row.event_kind.startswith("confirm_"):
        handler = OUTBOX_HANDLERS.get(("delivery_manual", "confirm"))
    return handler


def _jittered_backoff_seconds(attempt_count: int, event_id: int) -> int:
    """确定性 ±20% jitter，分散同批 Outbox 重试，避免雪崩式重试。"""
    base = _backoff_seconds(attempt_count)
    digest = hashlib.blake2s(
        f"{int(event_id)}:{int(attempt_count)}".encode("ascii"), digest_size=2
    ).digest()
    percent = int.from_bytes(digest, "big") % 41 - 20
    return max(1, min(BACKOFF_CAP_SEC, int(round(base * (100 + percent) / 100))))


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
    """回收崩溃残留的 PROCESSING 行。

    普通事件恢复为 PENDING；外部副作用事件进入结果未知死信，避免崩溃后盲目重复执行。
    """
    cutoff = now_local() - timedelta(seconds=stale_after)
    # delivery_manual/webhook_task 的外卖平台请求可能在进程崩溃前已送达。
    # 只对确认/取消/催单动作 fail-closed；print_ticket 由 PrintJob 自己管理，不能混入此规则。
    external_side_effect = or_(
        OutboxEvent.aggregate_type == "delivery_manual",
        and_(
            OutboxEvent.aggregate_type == "webhook_task",
            or_(
                OutboxEvent.payload.like('%"action": "confirm_order"%'),
                OutboxEvent.payload.like('%"action":"confirm_order"%'),
                OutboxEvent.payload.like('%"action": "cancel_order"%'),
                OutboxEvent.payload.like('%"action":"cancel_order"%'),
                OutboxEvent.payload.like('%"action": "reply_urge"%'),
                OutboxEvent.payload.like('%"action":"reply_urge"%'),
            ),
        ),
    )
    exhausted = OutboxEvent.attempt_count >= OutboxEvent.max_attempts
    stmt = (
        update(OutboxEvent)
        .where(
            OutboxEvent.status == "PROCESSING",
            or_(OutboxEvent.locked_at.is_(None), OutboxEvent.locked_at < cutoff),
        )
        .values(
            # 崩溃发生在最后一次允许尝试时，不能复位为可无限 claim 的 PENDING。
            status=case(
                (external_side_effect, "UNCERTAIN"),
                (exhausted, "FAILED"),
                else_="PENDING",
            ),
            next_retry_at=case(
                (or_(external_side_effect, exhausted), None),
                else_=OutboxEvent.next_retry_at,
            ),
            last_error=case(
                (
                    external_side_effect,
                    "EXTERNAL_RESULT_UNKNOWN: stale PROCESSING reclaimed after worker interruption; verify downstream platform before replay",
                ),
                (exhausted, "max_attempts_exhausted_after_stale_processing"),
                else_=OutboxEvent.last_error,
            ),
            locked_at=None,
            locked_by=None,
        )
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
    # 每次 claim 都追加唯一 token，避免同进程重领时旧 handler 的迟到完成误认新租约。
    worker_prefix = (worker or worker_id())[:30]
    wid = f"{worker_prefix}:{uuid.uuid4().hex}"[:64]
    now = now_local()
    stmt = (
        update(OutboxEvent)
        .where(
            OutboxEvent.id == outbox_id,
            or_(
                and_(
                    OutboxEvent.status == "PENDING",
                    OutboxEvent.attempt_count < OutboxEvent.max_attempts,
                ),
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


async def _heartbeat_outbox_lease(
    outbox_id: int,
    wid: str,
    *,
    interval: int = LEASE_HEARTBEAT_INTERVAL_SEC,
) -> bool:
    """独立短事务续租；返回 False 表示锁已被回收/转移。"""
    loop = asyncio.get_running_loop()
    failure_started_at: float | None = None
    while True:
        await asyncio.sleep(max(1, interval))
        try:
            async with asyncio.timeout(LEASE_HEARTBEAT_DB_TIMEOUT_SEC):
                async with AsyncSessionLocal() as lease_db:
                    result = await lease_db.execute(
                        update(OutboxEvent)
                        .where(
                            OutboxEvent.id == outbox_id,
                            OutboxEvent.status == "PROCESSING",
                            OutboxEvent.locked_by == wid,
                        )
                        .values(locked_at=now_local())
                    )
                    await lease_db.commit()
            if not result.rowcount:
                logger.warning("Outbox lease lost id=%s worker=%s", outbox_id, wid)
                return False
            failure_started_at = None
        except asyncio.CancelledError:
            raise
        except Exception:
            if failure_started_at is None:
                failure_started_at = loop.time()
            outage_seconds = loop.time() - failure_started_at
            logger.exception(
                "Outbox lease heartbeat failed id=%s worker=%s outage_seconds=%.1f",
                outbox_id, wid, outage_seconds,
            )
            # 不允许 DB 故障期间无限运行旧 handler：在 300 秒 stale reclaim 前
            # 提前取消，给当前 owner 留出安全余量，避免新旧 worker 并发外部调用。
            if outage_seconds >= LEASE_HEARTBEAT_MAX_FAILURE_SEC:
                logger.error(
                    "Outbox lease heartbeat unavailable too long; fail closed id=%s worker=%s",
                    outbox_id, wid,
                )
                return False


async def _run_handler_with_lease(
    handler: OutboxHandler,
    db: AsyncSession,
    row: OutboxEvent,
    *,
    outbox_id: int,
    wid: str,
) -> bool:
    """处理期间续租；租约丢失时取消 handler，禁止旧 owner 继续执行后续逻辑。"""
    handler_task = asyncio.create_task(handler(db, row))
    heartbeat_task = asyncio.create_task(_heartbeat_outbox_lease(outbox_id, wid))
    try:
        done, _ = await asyncio.wait(
            {handler_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if heartbeat_task in done and heartbeat_task.result() is False:
            if not handler_task.done():
                handler_task.cancel()
            await asyncio.gather(handler_task, return_exceptions=True)
            return False
        await handler_task
        return True
    finally:
        if not heartbeat_task.done():
            heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)


async def _mark_uncertain(db: AsyncSession, outbox_id: int, err: str, *, wid: str) -> None:
    """把外部结果未知事件直接转为 UNCERTAIN，必须人工核实平台状态后才能重放。"""
    result = await db.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == int(outbox_id),
            OutboxEvent.status == "PROCESSING",
            OutboxEvent.locked_by == wid,
        )
        .values(
            status="UNCERTAIN",
            last_error=("EXTERNAL_RESULT_UNKNOWN: " + str(err))[:500],
            next_retry_at=None,
            locked_at=None,
            locked_by=None,
        )
    )
    await db.commit()
    if int(result.rowcount or 0) != 1:
        logger.warning("Outbox uncertain result could not be marked by old owner id=%s", outbox_id)
        return
    logger.critical(
        "Outbox EXTERNAL RESULT UNKNOWN: id=%s; automatic retry disabled",
        outbox_id,
    )


async def _mark_done_after_handler(db: AsyncSession, outbox_id: int, *, wid: str) -> None:
    """handler 已成功后，即使辅助幂等标记失败也不能重发外部副作用。"""
    result = await db.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == int(outbox_id),
            OutboxEvent.status == "PROCESSING",
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
    await db.commit()
    if int(result.rowcount or 0) != 1:
        logger.critical(
            "Outbox handler succeeded but DONE state could not be persisted id=%s; inspect/reconcile before retry",
            outbox_id,
        )


def _is_external_side_effect_outbox(row: OutboxEvent) -> bool:
    """只对确实会调用外部平台的 Outbox 事件启用防重复副作用保护。"""
    if str(getattr(row, "aggregate_type", "") or "") not in {"delivery_manual", "webhook_task"}:
        return False
    try:
        payload = json.loads(getattr(row, "payload", None) or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and str(payload.get("action") or "").lower() in {
        "confirm_order", "cancel_order", "reply_urge",
    }


async def process_outbox_item(outbox_id: int) -> bool:
    """原子认领、租约续期、幂等分发；库存镜像按 sync_version 收敛而非一次性去重。"""
    async with AsyncSessionLocal() as db:
        row = await claim_outbox_item(db, outbox_id)
        if not row:
            return False
        wid = (row.locked_by or "")[:64]
        is_stock_mirror = (
            row.aggregate_type in ("delivery_stock", "delivery_stock_base")
            and row.event_kind == "mirror"
        )

        if not is_stock_mirror:
            try:
                from app.domains.messaging.idempotency import is_processed

                msg_id = f"{row.aggregate_type}:{row.aggregate_id}:{row.event_kind}"
                if await is_processed(db, scope="outbox", message_id=msg_id):
                    done_result = await db.execute(
                        update(OutboxEvent)
                        .where(OutboxEvent.id == outbox_id, OutboxEvent.locked_by == wid)
                        .values(
                            status="DONE", processed_at=now_local(), last_error=None,
                            next_retry_at=None, locked_at=None, locked_by=None,
                        )
                    )
                    from app.services.worker_common import rollback_if_claim_lost
                    if await rollback_if_claim_lost(db, done_result, model=OutboxEvent, row_id=outbox_id):
                        return True
                    await db.commit()
                    return True
            except Exception as exc:
                logger.exception("outbox 幂等预检失败 id=%s，停止处理并安排重试", outbox_id)
                await db.rollback()
                await _mark_failed(db, row, f"idempotency_precheck_failed: {exc}", wid=wid)
                return True

        if row.aggregate_type in ("webhook_task", "delivery_manual"):
            try:
                import app.services.delivery_tasks  # noqa: F401
            except Exception:
                logger.exception("Failed to import delivery_tasks handler registry")
        if row.aggregate_type == "reco_cache":
            try:
                from app.services.reco_cache import register_reco_cache_outbox_handler
                register_reco_cache_outbox_handler()
            except Exception:
                logger.exception("Failed to register recommendation cache invalidation handler")

        handler = _resolve_outbox_handler(row)
        if handler is None:
            logger.error("Outbox handler not registered: %s/%s id=%s", row.aggregate_type, row.event_kind, outbox_id)
            await _mark_failed(db, row, "handler_not_registered", wid=wid)
            return True
        try:
            json.loads(row.payload) if row.payload else {}
        except (TypeError, ValueError):
            await _mark_failed(db, row, "payload_invalid_json", wid=wid)
            return True

        handler_succeeded = False
        try:
            lease_owned = await _run_handler_with_lease(
                handler, db, row, outbox_id=outbox_id, wid=wid
            )
            if not lease_owned:
                return True
            # 仅外部平台副作用 handler 成功后启用防重复保护；普通 DB handler 仍必须按事务失败重试。
            handler_succeeded = (not is_stock_mirror) and _is_external_side_effect_outbox(row)

            expected_sync_version = int(getattr(row, "sync_version", 0) or 0)
            done_conditions = [
                OutboxEvent.id == outbox_id,
                OutboxEvent.locked_by == wid,
            ]
            if is_stock_mirror:
                done_conditions.append(OutboxEvent.sync_version == expected_sync_version)
            done_result = await db.execute(
                update(OutboxEvent)
                .where(*done_conditions)
                .values(
                    status="DONE", processed_at=now_local(), last_error=None,
                    next_retry_at=None, locked_at=None, locked_by=None,
                )
            )

            if is_stock_mirror and int(done_result.rowcount or 0) != 1:
                # 库存在平台同步期间再次变化：回滚旧快照副作用，仅在仍是本 owner 时重排最新绝对库存。
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
                    requeue = await db.execute(
                        update(OutboxEvent)
                        .where(
                            OutboxEvent.id == outbox_id,
                            OutboxEvent.status == "PROCESSING",
                            OutboxEvent.locked_by == wid,
                        )
                        .values(
                            status="PENDING", attempt_count=0, next_retry_at=None,
                            locked_at=None, locked_by=None, last_error=None,
                        )
                    )
                    if int(requeue.rowcount or 0) == 1:
                        await db.commit()
                    else:
                        await db.rollback()
                return True

            from app.services.worker_common import rollback_if_claim_lost
            if await rollback_if_claim_lost(db, done_result, model=OutboxEvent, row_id=outbox_id):
                return True

            idem_msg = f"{row.aggregate_type}:{row.aggregate_id}:{row.event_kind}"
            if not is_stock_mirror:
                try:
                    from app.domains.messaging.idempotency import try_acquire
                    acquired = await try_acquire(
                        db, scope="outbox", message_id=idem_msg,
                        ttl_seconds=86400 * 7, mark_redis=False,
                    )
                    if not acquired:
                        # handler 已成功执行；重复幂等标记不应触发外部副作用重放。
                        logger.warning(
                            "Outbox idempotency marker already exists after successful handler; completing event id=%s",
                            outbox_id,
                        )
                except Exception as exc:
                    # handler 已成功执行，辅助幂等记录失败不能把事件重新排队，否则会重复执行下游副作用。
                    logger.exception(
                        "Outbox idempotency marker write failed after handler success id=%s: %s",
                        outbox_id, exc,
                    )
                    await db.rollback()
                    await _mark_done_after_handler(db, outbox_id, wid=wid)
                    return True

            await db.commit()
            # 只有订单状态与 Outbox DONE 已一并提交后，才广播商家取消完成事件。
            if row.aggregate_type == "webhook_task":
                try:
                    completed_payload = json.loads(row.payload or "{}")
                    if completed_payload.get("action") == "cancel_order":
                        from app.services.websocket_manager import ws_manager

                        await ws_manager.notify_order_event(
                            "ORDER_CANCELLED",
                            {
                                "order_id": completed_payload.get("order_id"),
                                "reason": completed_payload.get("reason") or "商家取消",
                                "by": "merchant",
                            },
                        )
                except Exception:
                    # WebSocket 是瞬时通知；通知失败不能回滚已提交状态或重放平台副作用。
                    logger.warning("外卖取消完成通知发送失败 outbox_id=%s", outbox_id, exc_info=True)
            if not is_stock_mirror:
                try:
                    from app.domains.messaging.idempotency import mark_processed_redis
                    await mark_processed_redis(
                        scope="outbox", message_id=idem_msg, ttl_seconds=86400 * 7
                    )
                except Exception:
                    logger.warning("outbox 幂等 Redis 回填失败 id=%s", outbox_id)
        except ExternalSideEffectUncertain as exc:
            logger.error(
                "Outbox external side effect result is uncertain id=%s agg=%s/%s: %s",
                outbox_id, row.aggregate_type, row.event_kind, exc,
            )
            uncertain_outbox_id = int(row.id)
            await db.rollback()
            await _mark_uncertain(db, uncertain_outbox_id, str(exc), wid=wid)
        except Exception as exc:
            if handler_succeeded:
                logger.exception(
                    "Outbox handler succeeded but finalization failed; prevent duplicate side effect id=%s: %s",
                    outbox_id, exc,
                )
                await db.rollback()
                await _mark_done_after_handler(db, outbox_id, wid=wid)
                return True
            logger.exception("Outbox handler failed id=%s agg=%s/%s: %s", outbox_id, row.aggregate_type, row.event_kind, exc)
            await db.rollback()
            await _mark_failed(db, row, str(exc), wid=wid)
        return True

async def _mark_failed(db: AsyncSession, row: OutboxEvent, err: str, *, wid: str = "") -> None:
    """仅原 claim owner 可标记失败；退避带 jitter，耗尽时暴露 DEAD-LETTER 告警。"""
    fresh = await db.get(OutboxEvent, row.id)
    if not fresh or fresh.status == "DONE":
        return
    attempts = int(fresh.attempt_count or 1)
    max_att = int(fresh.max_attempts or MAX_ATTEMPTS)
    retry_at = (
        now_local() + timedelta(seconds=_jittered_backoff_seconds(attempts, int(row.id)))
        if attempts < max_att else None
    )
    fail_stmt = (
        update(OutboxEvent)
        .where(OutboxEvent.id == row.id, OutboxEvent.locked_by == wid)
        .values(
            status="FAILED", last_error=err[:500], locked_at=None,
            locked_by=None, next_retry_at=retry_at,
        )
    )
    fail_result = await db.execute(fail_stmt)
    await db.commit()
    if not fail_result.rowcount:
        logger.warning("Outbox 行在失败标记期间被 reclaim 释放 id=%s", row.id)
        return
    if attempts >= max_att:
        logger.critical(
            "Outbox DEAD-LETTER: id=%s aggregate=%s/%s attempts=%s max_attempts=%s error=%s",
            row.id, row.aggregate_type, row.aggregate_id, attempts, max_att, err[:300],
        )
    else:
        logger.warning(
            "Outbox retry scheduled: id=%s aggregate=%s/%s attempt=%s/%s retry_at=%s error=%s",
            row.id, row.aggregate_type, row.aggregate_id, attempts, max_att, retry_at, err[:300],
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
    try:
        import app.services.menu_cache_handlers  # noqa: F401
    except Exception:
        logger.exception("Failed to import menu_cache_handlers handlers")
    try:
        import app.services.delivery_tasks  # noqa: F401
    except Exception:
        logger.exception("Failed to import delivery_tasks handlers")
    try:
        from app.services.reco_cache import register_reco_cache_outbox_handler
        register_reco_cache_outbox_handler()
    except Exception:
        logger.exception("Failed to register recommendation cache invalidation handler")
    now = now_local()
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(OutboxEvent.id)
                .where(
                    or_(
                        and_(
                            OutboxEvent.status == "PENDING",
                            OutboxEvent.attempt_count < OutboxEvent.max_attempts,
                        ),
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
        # 返回值直接来自原子 claim 结果，不再通过状态前后差异推断。
        if await process_outbox_item(oid):
            claimed += 1
    return claimed



async def requeue_dead_letter_outbox(db: AsyncSession, outbox_id: int) -> bool:
    """仅重排已耗尽 FAILED 或需人工核实的 UNCERTAIN 事件；不触碰 PROCESSING/可重试事件。"""
    result = await db.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == int(outbox_id),
            or_(
                and_(
                    OutboxEvent.status == "FAILED",
                    OutboxEvent.attempt_count >= OutboxEvent.max_attempts,
                ),
                and_(
                    OutboxEvent.status == "UNCERTAIN",
                    OutboxEvent.last_error.like("EXTERNAL_RESULT_UNKNOWN:%"),
                ),
            ),
        )
        .values(
            status="PENDING",
            attempt_count=0,
            next_retry_at=None,
            locked_at=None,
            locked_by=None,
            processed_at=None,
        )
    )
    if int(result.rowcount or 0) != 1:
        return False
    await db.flush()
    return True

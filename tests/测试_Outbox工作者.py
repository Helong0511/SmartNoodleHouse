"""Outbox worker 单测：enqueue 幂等、claim 原子、stale 回收、退避、handler 注册。"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.services import outbox_worker  # noqa: F401 — 仅用于 patch 引用
from app.services.outbox_worker import (
    OUTBOX_HANDLERS,
    _backoff_seconds,
    claim_outbox_item,
    enqueue_outbox,
    reclaim_stale_processing,
    register_outbox_handler,
)

import asyncio


def test_backoff_seconds_caps_at_600():
    assert _backoff_seconds(0) == 1
    assert _backoff_seconds(1) == 2
    assert _backoff_seconds(2) == 4
    # 封顶 600
    assert _backoff_seconds(20) == 600
    assert _backoff_seconds(100) == 600


def test_register_outbox_handler_decorator():
    key = ("test_paid_side_effect", "points_award")
    previous = OUTBOX_HANDLERS.get(key)

    @register_outbox_handler(*key)
    async def _handler(db, row):
        return None

    assert key in OUTBOX_HANDLERS
    assert OUTBOX_HANDLERS[key] is _handler
    if previous is None:
        OUTBOX_HANDLERS.pop(key, None)
    else:
        OUTBOX_HANDLERS[key] = previous


@pytest.mark.asyncio
async def test_enqueue_outbox_idempotent_returns_existing():
    """第二次 enqueue 同 (agg,id,kind) 应返回已有行且 is_new=False。

    UNIQUE 冲突仅回滚 SAVEPOINT，不调用 db.rollback()，不破坏外部事务。
    """
    existing_row = MagicMock(id=99, status="PENDING")
    db = MagicMock()
    db.add = MagicMock()

    # SAVEPOINT 上下文：__aexit__ 不吞异常 → IntegrityError 传播到 except
    class _NestedCtx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

    db.begin_nested = MagicMock(return_value=_NestedCtx())
    # flush 在 savepoint 内抛 IntegrityError（UNIQUE 冲突），触发回查
    db.flush = AsyncMock(side_effect=IntegrityError("orig", {}, None))
    db.rollback = AsyncMock()
    # 回查返回 existing_row
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none = MagicMock(return_value=existing_row)
    db.execute = AsyncMock(return_value=scalar_result)

    row, is_new = await enqueue_outbox(
        db,
        aggregate_type="paid_side_effect",
        aggregate_id=123,
        event_kind="points_award",
        payload={"order_id": 123},
    )
    assert is_new is False
    assert row is existing_row
    # 关键断言：不得回滚外部事务
    assert db.rollback.await_count == 0


@pytest.mark.asyncio
async def test_claim_outbox_item_returns_none_when_rowcount_zero():
    """并发 race：另一 worker 先 claim 成功（rowcount=0）应返回 None。"""
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 0
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.get = AsyncMock(return_value=None)
    claimed = await claim_outbox_item(db, outbox_id=1, worker="wA")
    assert claimed is None
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_claim_outbox_item_pending_requires_attempts_below_maximum():
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 0
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    claimed = await claim_outbox_item(db, outbox_id=7, worker="wA")
    assert claimed is None
    stmt = db.execute.await_args.args[0]
    compiled = stmt.compile()
    assert "max_attempts" in str(stmt).lower()
    assert "attempt_count" in str(stmt).lower()
    assert "<" in str(stmt)


@pytest.mark.asyncio
async def test_claim_outbox_item_returns_row_on_success():
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 1
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    fetched = MagicMock(id=5, status="PROCESSING")
    db.get = AsyncMock(return_value=fetched)
    claimed = await claim_outbox_item(db, outbox_id=5, worker="wA")
    assert claimed is fetched


@pytest.mark.asyncio
async def test_reclaim_stale_processing_splits_retryable_and_exhausted_rows():
    db = MagicMock()
    result = MagicMock(rowcount=5)
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    n = await reclaim_stale_processing(db, stale_after=300)
    assert n == 5
    db.execute.assert_awaited_once()
    db.commit.assert_awaited_once()
    stmt = db.execute.await_args.args[0]
    compiled = stmt.compile()
    assert "outbox_events" in str(stmt).lower()
    assert "PROCESSING" in [str(v) for v in compiled.params.values()]
    assert "locked_at" in str(stmt).lower()
    # 单条 UPDATE + CASE 原子区分可重试与耗尽行，避免两次 UPDATE 之间发生状态竞争。
    assert "CASE" in str(stmt).upper()
    assert "FAILED" in [str(v) for v in compiled.params.values()]
    assert "UNCERTAIN" in [str(v) for v in compiled.params.values()]
    assert "PENDING" in [str(v) for v in compiled.params.values()]
    # 外部平台/设备副作用事件不能在 worker 崩溃后盲目回到 PENDING。
    assert "delivery_manual" in [str(v) for v in compiled.params.values()]
    assert "webhook_task" in [str(v) for v in compiled.params.values()]
    assert any('"action": "confirm_order"' in str(v) for v in compiled.params.values())
    assert any('"action": "cancel_order"' in str(v) for v in compiled.params.values())
    assert any('"action": "reply_urge"' in str(v) for v in compiled.params.values())
    assert not any("print_ticket" in str(v) for v in compiled.params.values())
    assert any("EXTERNAL_RESULT_UNKNOWN" in str(v) for v in compiled.params.values())


@pytest.mark.asyncio
async def test_mark_uncertain_moves_event_to_dead_letter_without_retry():
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    db.commit = AsyncMock()

    await outbox_worker._mark_uncertain(
        db, 41, "MEITUAN confirm order=7788: ReadTimeout", wid="worker-1"
    )

    stmt = db.execute.await_args.args[0]
    compiled = stmt.compile()
    assert "EXTERNAL_RESULT_UNKNOWN" in str(compiled.params.values())
    assert "UNCERTAIN" in [str(v) for v in compiled.params.values()]
    assert "PROCESSING" in [str(v) for v in compiled.params.values()]
    assert "worker-1" in [str(v) for v in compiled.params.values()]
    assert "last_error" in str(stmt)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_external_side_effect_read_timeout_is_uncertain_but_connect_error_can_retry():
    import httpx

    from app.services.delivery_tasks import _call_external_side_effect
    from app.services.outbox_worker import ExternalSideEffectUncertain

    request = httpx.Request("POST", "https://platform.invalid/order/confirm")

    async def read_timeout():
        raise httpx.ReadTimeout("read timed out", request=request)

    with pytest.raises(ExternalSideEffectUncertain):
        await _call_external_side_effect("confirm_order", "MEITUAN", "7788", read_timeout)

    async def connect_error():
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(httpx.ConnectError):
        await _call_external_side_effect("confirm_order", "MEITUAN", "7788", connect_error)

    async def server_error():
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("upstream unavailable", request=request, response=response)

    with pytest.raises(ExternalSideEffectUncertain):
        await _call_external_side_effect("confirm_order", "MEITUAN", "7788", server_error)

    async def conflict_error():
        response = httpx.Response(409, request=request)
        raise httpx.HTTPStatusError("order state conflict", request=request, response=response)

    with pytest.raises(ExternalSideEffectUncertain):
        await _call_external_side_effect("confirm_order", "MEITUAN", "7788", conflict_error)

    async def client_error():
        response = httpx.Response(400, request=request)
        raise httpx.HTTPStatusError("invalid request", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await _call_external_side_effect("confirm_order", "MEITUAN", "7788", client_error)


def test_unknown_external_result_requires_explicit_reconciliation_before_replay():
    from app.api.outbox_admin import (
        _is_external_result_unknown,
        _require_external_result_replay_confirmation,
        _require_external_result_success_confirmation,
    )
    from app.core.exceptions import AppException

    marker = "EXTERNAL_RESULT_UNKNOWN: ReadTimeout"
    assert _is_external_result_unknown(marker)
    assert not _is_external_result_unknown("ordinary provider failure")
    with pytest.raises(AppException):
        _require_external_result_replay_confirmation(marker, False, "")
    _require_external_result_replay_confirmation(marker, True, "下游核实未执行；订单仍待接单")
    with pytest.raises(AppException):
        _require_external_result_replay_confirmation("ordinary failure", True, "下游核实未执行；核对完成")
    with pytest.raises(AppException):
        _require_external_result_success_confirmation("未核实，先关闭")
    _require_external_result_success_confirmation("平台核实已成功；订单已确认")


@pytest.mark.asyncio
async def test_outbox_claim_uses_unique_per_attempt_owner_token():
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 1
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    fetched = MagicMock(id=9, status="PROCESSING")
    db.get = AsyncMock(return_value=fetched)

    await claim_outbox_item(db, outbox_id=9, worker="same-worker")
    stmt = db.execute.await_args.args[0]
    compiled = stmt.compile()
    owner_values = [str(v) for v in compiled.params.values()]
    assert any(
        v.startswith("same-worker:") and 32 < len(v) <= 64
        for v in owner_values
    )


@pytest.mark.asyncio
async def test_claim_lease_guard_cancels_owner_when_claim_is_lost(monkeypatch):
    import asyncio
    from app.models import OutboxEvent
    from app.services import worker_common

    class FakeResult:
        rowcount = 0

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, stmt):
            return FakeResult()

        async def commit(self):
            return None

    monkeypatch.setattr(worker_common, "AsyncSessionLocal", FakeSession)

    async def worker():
        async with worker_common.claim_lease_guard(
            OutboxEvent, 1, "owner-token", interval_seconds=1
        ):
            await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    await asyncio.sleep(1.1)
    with pytest.raises(asyncio.CancelledError):
        await task


def test_worker_lease_columns_use_mysql_microsecond_precision():
    """MySQL 秒精度会让 heartbeat 同秒更新被误判 rowcount=0。"""
    from sqlalchemy.dialects import mysql

    from app.models import OutboxEvent, WebhookInbox

    for model in (OutboxEvent, WebhookInbox):
        compiled = model.__table__.c.locked_at.type.compile(dialect=mysql.dialect())
        assert compiled.upper() == "DATETIME(6)"


@pytest.mark.asyncio
async def test_lost_claim_completion_rolls_back_entire_business_transaction():
    from app.models import OutboxEvent
    from app.services.worker_common import rollback_if_claim_lost

    db = MagicMock()
    db.rollback = AsyncMock()
    result = MagicMock(rowcount=0)

    rolled_back = await rollback_if_claim_lost(
        db, result, model=OutboxEvent, row_id=123
    )

    assert rolled_back is True
    assert db.rollback.await_count == 1


@pytest.mark.asyncio
async def test_owned_claim_completion_does_not_rollback():
    from app.models import OutboxEvent
    from app.services.worker_common import rollback_if_claim_lost

    db = MagicMock()
    db.rollback = AsyncMock()
    result = MagicMock(rowcount=1)

    rolled_back = await rollback_if_claim_lost(
        db, result, model=OutboxEvent, row_id=123
    )

    assert rolled_back is False
    assert db.rollback.await_count == 0


@pytest.mark.asyncio
async def test_unknown_claim_completion_rowcount_fails_closed():
    from app.models import OutboxEvent
    from app.services.worker_common import rollback_if_claim_lost

    db = MagicMock()
    db.rollback = AsyncMock()
    result = MagicMock(rowcount=None)

    rolled_back = await rollback_if_claim_lost(
        db, result, model=OutboxEvent, row_id=123
    )

    assert rolled_back is True
    assert db.rollback.await_count == 1


@pytest.mark.asyncio
async def test_successful_handler_is_not_retried_when_post_handler_idempotency_write_fails(monkeypatch):
    from app.domains.messaging import idempotency
    from app.services import outbox_worker

    row = MagicMock(
        id=903,
        aggregate_type="webhook_task",
        aggregate_id=99,
        event_kind="execute",
        locked_by="worker:external-token",
        payload='{"action":"cancel_order"}',
        sync_version=0,
    )
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *args):
            return False

    handler = AsyncMock()
    old_handler = outbox_worker.OUTBOX_HANDLERS.get(("webhook_task", "execute"))
    outbox_worker.OUTBOX_HANDLERS[("webhook_task", "execute")] = handler
    monkeypatch.setattr(outbox_worker, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(outbox_worker, "claim_outbox_item", AsyncMock(return_value=row))
    monkeypatch.setattr(idempotency, "is_processed", AsyncMock(return_value=False))
    monkeypatch.setattr(
        idempotency, "try_acquire",
        AsyncMock(side_effect=RuntimeError("idempotency database unavailable")),
    )

    async def _run_handler(handler_fn, _db, _row, **kwargs):
        await handler_fn(_db, _row)
        return True

    monkeypatch.setattr(outbox_worker, "_run_handler_with_lease", AsyncMock(side_effect=_run_handler))
    try:
        assert await outbox_worker.process_outbox_item(903) is True
    finally:
        if old_handler is None:
            outbox_worker.OUTBOX_HANDLERS.pop(("webhook_task", "execute"), None)
        else:
            outbox_worker.OUTBOX_HANDLERS[("webhook_task", "execute")] = old_handler

    handler.assert_awaited_once()
    db.rollback.assert_awaited_once()
    db.commit.assert_awaited_once()
    done_stmt = db.execute.await_args_list[-1].args[0]
    assert "DONE" in [str(v) for v in done_stmt.compile().params.values()]


@pytest.mark.asyncio
async def test_successful_handler_is_not_retried_when_done_update_fails(monkeypatch):
    from app.domains.messaging import idempotency
    from app.services import outbox_worker

    row = MagicMock(
        id=904,
        aggregate_type="webhook_task",
        aggregate_id=100,
        event_kind="execute",
        locked_by="worker:external-token",
        payload='{"action":"cancel_order"}',
        sync_version=0,
    )
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[RuntimeError("temporary database failure"), MagicMock(rowcount=1)]
    )
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *args):
            return False

    handler = AsyncMock()
    old_handler = outbox_worker.OUTBOX_HANDLERS.get(("webhook_task", "execute"))
    outbox_worker.OUTBOX_HANDLERS[("webhook_task", "execute")] = handler
    monkeypatch.setattr(outbox_worker, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(outbox_worker, "claim_outbox_item", AsyncMock(return_value=row))
    monkeypatch.setattr(idempotency, "is_processed", AsyncMock(return_value=False))
    try_acquire = AsyncMock()
    monkeypatch.setattr(idempotency, "try_acquire", try_acquire)
    mark_failed = AsyncMock()
    monkeypatch.setattr(outbox_worker, "_mark_failed", mark_failed)

    async def _run_handler(handler_fn, _db, _row, **kwargs):
        await handler_fn(_db, _row)
        return True

    monkeypatch.setattr(outbox_worker, "_run_handler_with_lease", AsyncMock(side_effect=_run_handler))
    try:
        assert await outbox_worker.process_outbox_item(904) is True
    finally:
        if old_handler is None:
            outbox_worker.OUTBOX_HANDLERS.pop(("webhook_task", "execute"), None)
        else:
            outbox_worker.OUTBOX_HANDLERS[("webhook_task", "execute")] = old_handler

    handler.assert_awaited_once()
    try_acquire.assert_not_awaited()
    mark_failed.assert_not_awaited()
    db.rollback.assert_awaited_once()
    db.commit.assert_awaited_once()
    done_stmt = db.execute.await_args_list[-1].args[0]
    assert "DONE" in [str(v) for v in done_stmt.compile().params.values()]


@pytest.mark.asyncio
async def test_internal_database_handler_remains_retryable_after_finalize_failure(monkeypatch):
    """普通内部 DB handler 事务提交失败时，不能错误标记 DONE。"""
    from app.domains.messaging import idempotency
    from app.services import outbox_worker

    row = MagicMock(
        id=905, aggregate_type="test_internal", aggregate_id=101,
        event_kind="update", locked_by="worker:internal-token",
        payload="{}", sync_version=0,
    )
    db = MagicMock()
    db.execute = AsyncMock(side_effect=RuntimeError("database finalize failed"))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    class _SessionContext:
        async def __aenter__(self):
            return db
        async def __aexit__(self, *args):
            return False

    handler = AsyncMock()
    old_handler = outbox_worker.OUTBOX_HANDLERS.get(("test_internal", "update"))
    outbox_worker.OUTBOX_HANDLERS[("test_internal", "update")] = handler
    monkeypatch.setattr(outbox_worker, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(outbox_worker, "claim_outbox_item", AsyncMock(return_value=row))
    monkeypatch.setattr(idempotency, "is_processed", AsyncMock(return_value=False))
    mark_failed = AsyncMock()
    monkeypatch.setattr(outbox_worker, "_mark_failed", mark_failed)
    mark_done = AsyncMock()
    monkeypatch.setattr(outbox_worker, "_mark_done_after_handler", mark_done)

    async def _run_handler(handler_fn, _db, _row, **kwargs):
        await handler_fn(_db, _row)
        return True

    monkeypatch.setattr(
        outbox_worker, "_run_handler_with_lease", AsyncMock(side_effect=_run_handler)
    )
    try:
        assert await outbox_worker.process_outbox_item(905) is True
    finally:
        if old_handler is None:
            outbox_worker.OUTBOX_HANDLERS.pop(("test_internal", "update"), None)
        else:
            outbox_worker.OUTBOX_HANDLERS[("test_internal", "update")] = old_handler

    handler.assert_awaited_once()
    db.rollback.assert_awaited_once()
    mark_failed.assert_awaited_once()
    mark_done.assert_not_awaited()

@pytest.mark.asyncio
async def test_库存镜像复用事件不得被历史一次性幂等记录跳过(monkeypatch):
    from contextlib import asynccontextmanager
    from app.domains.messaging import idempotency
    from app.services import outbox_worker

    row = MagicMock(
        id=901,
        aggregate_type="delivery_stock",
        aggregate_id=77,
        event_kind="mirror",
        locked_by="worker:stock-token",
        payload='{"store_id":1,"product_id":2}',
        sync_version=4,
    )
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=None)
    handler = AsyncMock()

    monkeypatch.setattr(outbox_worker, "AsyncSessionLocal", lambda: ctx)
    monkeypatch.setattr(outbox_worker, "claim_outbox_item", AsyncMock(return_value=row))

    async def _run_handler(handler_fn, _db, _row, **kwargs):
        await handler_fn(_db, _row)
        return True

    monkeypatch.setattr(
        outbox_worker,
        "_run_handler_with_lease",
        AsyncMock(side_effect=_run_handler),
    )
    old_handler = outbox_worker.OUTBOX_HANDLERS.get(("delivery_stock", "mirror"))
    outbox_worker.OUTBOX_HANDLERS[("delivery_stock", "mirror")] = handler
    monkeypatch.setattr(idempotency, "is_processed", AsyncMock(return_value=True))
    try:
        await outbox_worker.process_outbox_item(901)
    finally:
        if old_handler is None:
            outbox_worker.OUTBOX_HANDLERS.pop(("delivery_stock", "mirror"), None)
        else:
            outbox_worker.OUTBOX_HANDLERS[("delivery_stock", "mirror")] = old_handler

    handler.assert_awaited_once()
    idempotency.is_processed.assert_not_awaited()
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_库存镜像处理期间版本变化时重新排队而不是标记完成(monkeypatch):
    from contextlib import asynccontextmanager
    from app.services import outbox_worker

    row = MagicMock(
        id=902,
        aggregate_type="delivery_stock_base",
        aggregate_id=88,
        event_kind="mirror",
        locked_by="worker:stock-token",
        payload='{"store_id":1,"product_id":2}',
        sync_version=10,
    )
    fresh = MagicMock(id=902, status="PROCESSING", locked_by="worker:stock-token")
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = fresh
    done_result = MagicMock(rowcount=0)  # sync_version 条件不匹配
    requeue_result = MagicMock(rowcount=1)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[done_result, select_result, requeue_result])
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=None)
    handler = AsyncMock()

    monkeypatch.setattr(outbox_worker, "AsyncSessionLocal", lambda: ctx)
    monkeypatch.setattr(outbox_worker, "claim_outbox_item", AsyncMock(return_value=row))

    async def _run_handler(handler_fn, _db, _row, **kwargs):
        await handler_fn(_db, _row)
        return True

    monkeypatch.setattr(
        outbox_worker,
        "_run_handler_with_lease",
        AsyncMock(side_effect=_run_handler),
    )
    old_handler = outbox_worker.OUTBOX_HANDLERS.get(("delivery_stock_base", "mirror"))
    outbox_worker.OUTBOX_HANDLERS[("delivery_stock_base", "mirror")] = handler
    try:
        await outbox_worker.process_outbox_item(902)
    finally:
        if old_handler is None:
            outbox_worker.OUTBOX_HANDLERS.pop(("delivery_stock_base", "mirror"), None)
        else:
            outbox_worker.OUTBOX_HANDLERS[("delivery_stock_base", "mirror")] = old_handler

    handler.assert_awaited_once()
    assert db.rollback.await_count == 1
    assert db.commit.await_count == 1
    requeue_stmt = db.execute.await_args_list[2].args[0]
    assert "PENDING" in [str(v) for v in requeue_stmt.compile().params.values()]


@pytest.mark.asyncio
async def test_process_outbox_item_returns_false_when_atomic_claim_is_lost(monkeypatch):
    from app.services import outbox_worker

    class _Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(outbox_worker, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(
        outbox_worker, "claim_outbox_item", AsyncMock(return_value=None)
    )

    assert await outbox_worker.process_outbox_item(404) is False


@pytest.mark.asyncio
async def test_outbox_idempotency_precheck_failure_never_runs_handler(monkeypatch):
    from app.domains.messaging import idempotency
    from app.services import outbox_worker

    row = MagicMock(
        id=88,
        aggregate_type="paid_side_effect",
        aggregate_id=1234,
        event_kind="points_award",
        locked_by="worker:unique-token",
        payload='{"order_id":1234}',
    )
    db = MagicMock()
    db.rollback = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=None)
    handler = AsyncMock()

    monkeypatch.setattr(outbox_worker, "AsyncSessionLocal", lambda: ctx)
    monkeypatch.setattr(
        outbox_worker, "claim_outbox_item", AsyncMock(return_value=row)
    )
    monkeypatch.setattr(
        idempotency, "is_processed", AsyncMock(side_effect=RuntimeError("db unavailable"))
    )
    monkeypatch.setattr(outbox_worker, "_mark_failed", AsyncMock())
    old_handler = outbox_worker.OUTBOX_HANDLERS.get(("paid_side_effect", "points_award"))
    outbox_worker.OUTBOX_HANDLERS[("paid_side_effect", "points_award")] = handler
    try:
        claimed = await outbox_worker.process_outbox_item(88)
    finally:
        if old_handler is None:
            outbox_worker.OUTBOX_HANDLERS.pop(("paid_side_effect", "points_award"), None)
        else:
            outbox_worker.OUTBOX_HANDLERS[("paid_side_effect", "points_award")] = old_handler

    assert claimed is True
    assert db.rollback.await_count == 1
    outbox_worker._mark_failed.assert_awaited_once()
    handler.assert_not_awaited()


def test_webhook_deferred_event_kinds_resolve_to_shared_handler():
    from app.services import delivery_tasks  # noqa: F401 — register handler
    from app.models import OutboxEvent
    from app.services.outbox_worker import _resolve_outbox_handler

    handler = OUTBOX_HANDLERS[("webhook_task", "execute")]
    for event_kind in ("deferred_0000", "deferred_0001", "deferred_0099"):
        row = MagicMock(
            aggregate_type="webhook_task",
            event_kind=event_kind,
        )
        assert _resolve_outbox_handler(row) is handler


def test_reco_cache_invalidation_events_are_persisted_and_have_unique_event_kinds():
    """同一顾客多次画像修改必须创建独立 Outbox 事件，不能被历史 DONE 去重。"""
    from app.services.reco_cache import defer_invalidate_customer

    from sqlalchemy.orm import Session

    class FakeDB:
        def __init__(self):
            self.sync_session = Session()
            self.rows = []

        def close(self):
            self.sync_session.close()

        def add(self, row):
            self.rows.append(row)

    db = FakeDB()
    defer_invalidate_customer(db, 123)
    defer_invalidate_customer(db, 123)

    assert len(db.rows) == 2
    assert all(row.aggregate_type == "reco_cache" for row in db.rows)
    assert all(row.aggregate_id == 123 for row in db.rows)
    assert all(row.event_kind.startswith("invalidate_") for row in db.rows)
    assert db.rows[0].event_kind != db.rows[1].event_kind
    assert all('"customer_id":123' in row.payload for row in db.rows)
    db.close()


def test_reco_cache_outbox_dynamic_event_kinds_resolve_to_handler():
    from app.services.reco_cache import register_reco_cache_outbox_handler
    from app.services.outbox_worker import _resolve_outbox_handler

    register_reco_cache_outbox_handler()
    row = MagicMock(aggregate_type="reco_cache", event_kind="invalidate_0123456789abcdef")
    assert _resolve_outbox_handler(row) is not None


@pytest.mark.asyncio
async def test_reco_cache_outbox_handler_raises_when_redis_is_unavailable(monkeypatch):
    import json

    from app.services import reco_cache
    from app.services.outbox_worker import OUTBOX_HANDLERS

    async def _redis_down():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(reco_cache, "get_redis", _redis_down)
    reco_cache.register_reco_cache_outbox_handler()
    handler = OUTBOX_HANDLERS[("reco_cache", "invalidate")]
    row = MagicMock(
        aggregate_id=123,
        payload=json.dumps({"customer_id": 123}),
    )

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await handler(MagicMock(), row)


@pytest.mark.asyncio
async def test_outbox_retry_exhaustion_emits_critical_dead_letter_log(caplog):
    from types import SimpleNamespace
    import logging

    from app.services.outbox_worker import _mark_failed

    row = SimpleNamespace(
        id=991,
        aggregate_type="payment",
        aggregate_id=42,
    )
    fresh = SimpleNamespace(
        id=991,
        status="PROCESSING",
        attempt_count=8,
        max_attempts=8,
    )
    result = MagicMock(rowcount=1)
    db = MagicMock()
    db.get = AsyncMock(return_value=fresh)
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()

    with caplog.at_level(logging.CRITICAL, logger="app.services.outbox_worker"):
        await _mark_failed(db, row, "provider permanently rejected", wid="worker-token")

    assert "Outbox DEAD-LETTER" in caplog.text
    assert "991" in caplog.text
    assert "8" in caplog.text
    db.commit.assert_awaited_once()


def test_outbox_retry_jitter_is_deterministic_bounded_and_spread():
    from app.services.outbox_worker import (
        BACKOFF_CAP_SEC,
        _jittered_backoff_seconds,
    )

    first = _jittered_backoff_seconds(8, 1001)
    assert first == _jittered_backoff_seconds(8, 1001)
    assert 1 <= first <= 600

    delays = {_jittered_backoff_seconds(8, event_id) for event_id in range(1000, 1100)}
    assert len(delays) > 10

    capped = [
        _jittered_backoff_seconds(30, event_id)
        for event_id in range(1000, 1100)
    ]
    assert all(1 <= delay <= BACKOFF_CAP_SEC for delay in capped)
    assert len(set(capped)) > 5


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_kind", "service_module", "service_name"),
    [
        ("social_benefit_reverse", "app.services.social_benefit_service", "SocialBenefitService"),
        ("campaign_attribution_reverse", "app.services.attribution_service", "AttributionService"),
    ],
)
async def test_refund_reconciliation_handlers_lock_order_and_dispatch(
    monkeypatch, event_kind, service_module, service_name
):
    """退款后公益/营销冲减由 Outbox 分发，并先锁订单串行化同单退款。"""
    import importlib

    from app.models import Order, PaymentRefund
    from app.services import paid_side_effects  # noqa: F401 — 注册退款 handler

    module = importlib.import_module(service_module)
    service = getattr(module, service_name)
    method_name = "reverse_for_order"
    handler = OUTBOX_HANDLERS[("refund", event_kind)]
    reverse = AsyncMock()
    monkeypatch.setattr(service, method_name, reverse)

    refund = MagicMock(id=501, order_id=88)
    order = MagicMock(id=88)
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = order
    db = MagicMock()
    db.get = AsyncMock(return_value=refund)
    db.execute = AsyncMock(return_value=scalar_result)

    row = MagicMock(aggregate_id=501)
    await handler(db, row)

    db.get.assert_awaited_once_with(PaymentRefund, 501)
    stmt = db.execute.await_args.args[0]
    assert "FOR UPDATE" in str(stmt.compile()).upper()
    reverse.assert_awaited_once_with(db, 88)


def test_refund_reconciliation_handlers_are_registered():
    from app.services import paid_side_effects  # noqa: F401 — 注册退款 handler

    assert ("refund", "social_benefit_reverse") in OUTBOX_HANDLERS
    assert ("refund", "campaign_attribution_reverse") in OUTBOX_HANDLERS


@pytest.mark.asyncio
async def test_outbox_heartbeat_fails_closed_after_sustained_database_errors(monkeypatch):
    from app.services import outbox_worker

    class BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("database unavailable")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(outbox_worker, "AsyncSessionLocal", BrokenSession)
    monkeypatch.setattr(outbox_worker, "LEASE_HEARTBEAT_MAX_FAILURE_SEC", 0)
    monkeypatch.setattr(outbox_worker.asyncio, "sleep", AsyncMock())

    owned = await outbox_worker._heartbeat_outbox_lease(
        501, "worker-token", interval=1
    )

    assert owned is False


@pytest.mark.asyncio
async def test_handler_is_cancelled_when_lease_is_lost(monkeypatch):
    """租约 owner 已变更时，旧 handler 必须被取消，不能继续写 DONE。"""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_handler(db, row):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def lost_heartbeat(*args, **kwargs):
        await started.wait()
        return False

    monkeypatch.setattr(outbox_worker, "_heartbeat_outbox_lease", lost_heartbeat)
    result = await outbox_worker._run_handler_with_lease(
        slow_handler, MagicMock(), MagicMock(), outbox_id=9, wid="owner-a"
    )
    assert result is False
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_stock_menu_invalidation_outbox_is_enqueued_in_business_transaction(monkeypatch):
    from app.services import stock_service, menu_cache_service

    enqueue = AsyncMock(return_value=(MagicMock(), True))
    deduct = AsyncMock()
    monkeypatch.setattr(menu_cache_service, "enqueue_outbox", enqueue)
    monkeypatch.setattr(stock_service, "deduct_stock", deduct)

    await stock_service.deduct_for_order_items(
        MagicMock(),
        [{"product_id": 11, "quantity": 2}, {"product_id": 12, "quantity": 1}],
        store_id=7,
    )

    assert deduct.await_count == 2
    enqueue.assert_awaited_once()
    kwargs = enqueue.await_args.kwargs
    assert kwargs["aggregate_type"] == "menu_cache"
    assert kwargs["event_kind"] == "invalidate"
    assert 0 < kwargs["aggregate_id"] < (1 << 63)
    assert kwargs["payload"] == {"store_id": 7}


@pytest.mark.asyncio
async def test_menu_cache_invalidation_outbox_handler_retries_when_redis_unavailable(monkeypatch):
    import json
    from types import SimpleNamespace
    from app.services import recommendation_service
    from app.services import menu_cache_handlers

    invalidate = AsyncMock(return_value=False)
    monkeypatch.setattr(recommendation_service, "invalidate_store_caches", invalidate)
    row = SimpleNamespace(payload=json.dumps({"store_id": 7}), aggregate_id=123)

    with pytest.raises(RuntimeError, match="菜单缓存失效失败"):
        await menu_cache_handlers.menu_cache_invalidate_handler(MagicMock(), row)
    invalidate.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_menu_cache_invalidation_outbox_handler_invalidates_after_commit_worker(monkeypatch):
    import json
    from types import SimpleNamespace
    from app.services import recommendation_service
    from app.services import menu_cache_handlers

    invalidate = AsyncMock(return_value=True)
    monkeypatch.setattr(recommendation_service, "invalidate_store_caches", invalidate)
    row = SimpleNamespace(payload=json.dumps({"store_id": 8}), aggregate_id=124)

    await menu_cache_handlers.menu_cache_invalidate_handler(MagicMock(), row)
    invalidate.assert_awaited_once_with(8)


@pytest.mark.asyncio
async def test_process_pending_outbox_counts_claim_result_not_final_status(monkeypatch):
    """FAILED→PROCESSING→FAILED 仍应计入本轮 claim；未抢到的事件不计数。"""
    from app.services import outbox_worker

    class _Result:
        def scalars(self):
            return self
        def all(self):
            return [101, 102]

    class _Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def execute(self, stmt):
            return _Result()

    monkeypatch.setattr(outbox_worker, "AsyncSessionLocal", _Session)
    process = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(outbox_worker, "process_outbox_item", process)

    assert await outbox_worker.process_pending_outbox(limit=2) == 1
    assert [call.args[0] for call in process.await_args_list] == [101, 102]


@pytest.mark.asyncio
async def test_requeue_dead_letter_only_updates_exhausted_failed_rows():
    from app.models import OutboxEvent
    from app.services.outbox_worker import requeue_dead_letter_outbox

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    db.flush = AsyncMock()

    assert await requeue_dead_letter_outbox(db, 321) is True
    db.flush.assert_awaited_once()
    stmt = db.execute.await_args.args[0]
    compiled = stmt.compile()
    assert "outbox_events.status" in str(compiled)
    assert "outbox_events.attempt_count" in str(compiled)
    assert "PENDING" in compiled.params.values()
    assert 0 in compiled.params.values()


@pytest.mark.asyncio
async def test_requeue_dead_letter_fails_closed_when_row_changed_or_not_exhausted():
    from app.services.outbox_worker import requeue_dead_letter_outbox

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=0))
    db.flush = AsyncMock()

    assert await requeue_dead_letter_outbox(db, 321) is False
    db.flush.assert_not_awaited()


def test_outbox_dead_letter_admin_api_is_super_admin_only():
    from types import SimpleNamespace

    from app.core.exceptions import AppException
    from app.api.outbox_admin import _require_super_admin

    with pytest.raises(AppException):
        _require_super_admin(SimpleNamespace(role=SimpleNamespace(code="STORE_ADMIN")))
    with pytest.raises(AppException):
        _require_super_admin(SimpleNamespace(role=SimpleNamespace(code="CASHIER")))
    _require_super_admin(SimpleNamespace(role=SimpleNamespace(code="SUPER_ADMIN")))


def test_outbox_dead_letter_error_display_redacts_common_credentials():
    from app.api.outbox_admin import _safe_error

    value = "Authorization: Bearer abc.def password=hunter2 token=secret-value upstream unavailable"
    safe = _safe_error(value)
    assert "abc.def" not in safe
    assert "hunter2" not in safe
    assert "secret-value" not in safe
    assert "***" in safe
    assert "upstream unavailable" in safe
    json_safe = _safe_error('{"access_token":"json-secret","apiAuthToken":"app-secret"}')
    assert "json-secret" not in json_safe
    assert "app-secret" not in json_safe



def test_manual_delivery_confirm_retry_event_kinds_resolve_to_shared_handler():
    """每次失败后的新接单尝试使用 confirm_<uuid>，仍由白名单 handler 执行。"""
    from types import SimpleNamespace
    from app.services import delivery_tasks  # noqa: F401 — register handler
    from app.services.outbox_worker import OUTBOX_HANDLERS, _resolve_outbox_handler

    handler = OUTBOX_HANDLERS[("delivery_manual", "confirm")]
    for event_kind in ("confirm_0123456789abcdef", "confirm_abcdef0123456789"):
        row = SimpleNamespace(aggregate_type="delivery_manual", event_kind=event_kind)
        assert _resolve_outbox_handler(row) is handler


@pytest.mark.asyncio
async def test_malformed_platform_response_is_uncertain_and_never_auto_retried():
    from app.services.delivery_providers import DeliveryPlatformResultUnknown
    from app.services.delivery_tasks import _call_external_side_effect
    from app.services.outbox_worker import ExternalSideEffectUncertain

    async def malformed_success_http_response():
        raise DeliveryPlatformResultUnknown(
            "MEITUAN /api/v1/order/cancel: HTTP 200 response is not valid JSON"
        )

    with pytest.raises(ExternalSideEffectUncertain, match="响应格式/结构异常"):
        await _call_external_side_effect(
            "cancel_order", "MEITUAN", "MT-ORDER-UNKNOWN",
            malformed_success_http_response,
        )


@pytest.mark.asyncio
async def test_legacy_deferred_confirm_does_not_rollback_on_read_timeout(monkeypatch):
    import httpx
    from unittest.mock import AsyncMock, MagicMock

    from app.services import delivery_tasks

    request = httpx.Request("POST", "https://platform.invalid/confirm")
    provider = MagicMock()

    async def timeout(_platform_order_id):
        raise httpx.ReadTimeout("response lost", request=request)

    provider.confirm_order = timeout
    monkeypatch.setattr(delivery_tasks, "get_delivery_provider", lambda *_args: provider)
    rollback = AsyncMock()
    monkeypatch.setattr(delivery_tasks, "_rollback_confirm", rollback)

    await delivery_tasks.run_deferred_tasks([{
        "action": "confirm_order",
        "platform": "MEITUAN",
        "platform_order_id": "MT-UNKNOWN-1",
        "rollback_on_fail": True,
    }])

    rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_deferred_confirm_rolls_back_only_on_explicit_false(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.services import delivery_tasks

    provider = MagicMock()
    provider.confirm_order = AsyncMock(return_value=False)
    monkeypatch.setattr(delivery_tasks, "get_delivery_provider", lambda *_args: provider)
    rollback = AsyncMock()
    monkeypatch.setattr(delivery_tasks, "_rollback_confirm", rollback)

    await delivery_tasks.run_deferred_tasks([{
        "action": "confirm_order",
        "platform": "MEITUAN",
        "platform_order_id": "MT-REJECTED-1",
        "rollback_on_fail": True,
    }])

    rollback.assert_awaited_once_with(None, "MEITUAN", "MT-REJECTED-1")

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


def test_backoff_seconds_caps_at_600():
    assert _backoff_seconds(0) == 1
    assert _backoff_seconds(1) == 2
    assert _backoff_seconds(2) == 4
    # 封顶 600
    assert _backoff_seconds(20) == 600
    assert _backoff_seconds(100) == 600


def test_register_outbox_handler_decorator():
    OUTBOX_HANDLERS.clear()

    @register_outbox_handler("paid_side_effect", "points_award")
    async def _handler(db, row):
        return None

    assert ("paid_side_effect", "points_award") in OUTBOX_HANDLERS
    assert OUTBOX_HANDLERS[("paid_side_effect", "points_award")] is _handler
    OUTBOX_HANDLERS.clear()


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
async def test_reclaim_stale_processing_issues_update_and_commits():
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 3
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    n = await reclaim_stale_processing(db, stale_after=300)
    assert n == 3
    assert db.commit.await_count == 1
    # 验证 UPDATE 语句 WHERE 针对 outbox_events 的 status + locked_at
    stmt = db.execute.await_args[0][0]
    stmt_str = str(stmt).lower()
    assert "outbox_events" in stmt_str
    assert "status" in stmt_str
    assert "locked_at" in stmt_str
    # 编译后的绑定参数应包含 PROCESSING 值
    compiled = stmt.compile()
    assert "PROCESSING" in [str(v) for v in compiled.params.values()]


@pytest.mark.asyncio
async def test_outbox_claim_uses_unique_per_attempt_owner_token():
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 1
    db.execute = AsyncMock(return_value=result)
    fetched = MagicMock(id=9, status="PROCESSING")
    db.get = AsyncMock(return_value=fetched)

    await claim_outbox_item(db, outbox_id=9, worker="same-worker")
    stmt = db.execute.await_args.args[0]
    compiled = stmt.compile()
    owner_values = [str(v) for v in compiled.params.values()]
    assert any(v.startswith("same-worker:") and len(v) == 64 for v in owner_values)


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

    @asynccontextmanager
    async def _guard(*args, **kwargs):
        yield

    monkeypatch.setattr(outbox_worker, "claim_lease_guard", _guard)
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

    @asynccontextmanager
    async def _guard(*args, **kwargs):
        yield

    monkeypatch.setattr(outbox_worker, "claim_lease_guard", _guard)
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
        await outbox_worker.process_outbox_item(88)
    finally:
        if old_handler is None:
            outbox_worker.OUTBOX_HANDLERS.pop(("paid_side_effect", "points_award"), None)
        else:
            outbox_worker.OUTBOX_HANDLERS[("paid_side_effect", "points_award")] = old_handler

    assert db.rollback.await_count == 1
    outbox_worker._mark_failed.assert_awaited_once()
    handler.assert_not_awaited()

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

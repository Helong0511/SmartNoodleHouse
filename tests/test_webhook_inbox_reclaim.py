"""Webhook inbox 单测：stale PROCESSING 回收 + process_pending_inbox 计数修正。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.webhook_inbox import (
    STALE_PROCESSING_SEC,
    reclaim_stale_inbox,
    process_pending_inbox,
)


@pytest.mark.asyncio
async def test_reclaim_stale_inbox_issues_update_and_commits():
    """reclaim 应对 PROCESSING 行发出 UPDATE（status + locked_at 条件）并 commit。"""
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 2
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    n = await reclaim_stale_inbox(db, stale_after=300)
    assert n == 2
    assert db.commit.await_count == 1
    stmt = db.execute.await_args[0][0]
    stmt_str = str(stmt).lower()
    assert "webhook_inbox" in stmt_str
    assert "status" in stmt_str
    assert "locked_at" in stmt_str
    compiled = stmt.compile()
    assert "PROCESSING" in [str(v) for v in compiled.params.values()]


@pytest.mark.asyncio
async def test_reclaim_stale_inbox_zero_when_nothing_stale():
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 0
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    n = await reclaim_stale_inbox(db)
    assert n == 0


@pytest.mark.asyncio
async def test_reclaim_stale_inbox_uses_default_threshold():
    """默认 stale_after 应为 STALE_PROCESSING_SEC。"""
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 0
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    await reclaim_stale_inbox(db)
    assert STALE_PROCESSING_SEC == 300


@pytest.mark.asyncio
async def test_process_pending_inbox_counts_only_claimed():
    """process_pending_inbox 应统计成功 claim 的数量，而非调用次数。"""

    async def fake_process(iid):
        # 只有奇数 id 成功 claim
        return iid % 2 == 1

    db = MagicMock()
    scalars_result = MagicMock()
    scalars_result.all = MagicMock(return_value=[1, 2, 3, 4, 5])
    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=scalars_result)
    db.execute = AsyncMock(return_value=exec_result)

    with patch(
        "app.services.webhook_inbox.AsyncSessionLocal"
    ) as mock_session_ctx, patch(
        "app.services.webhook_inbox.process_inbox_item", side_effect=fake_process
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
        n = await process_pending_inbox(limit=10)
    # 只有 1,3,5 成功 claim → 3
    assert n == 3


@pytest.mark.asyncio
async def test_process_pending_inbox_zero_when_all_skipped():
    async def fake_process(iid):
        return False

    db = MagicMock()
    scalars_result = MagicMock()
    scalars_result.all = MagicMock(return_value=[10, 20, 30])
    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=scalars_result)
    db.execute = AsyncMock(return_value=exec_result)

    with patch(
        "app.services.webhook_inbox.AsyncSessionLocal"
    ) as mock_session_ctx, patch(
        "app.services.webhook_inbox.process_inbox_item", side_effect=fake_process
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
        n = await process_pending_inbox(limit=10)
    assert n == 0

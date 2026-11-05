"""Webhook inbox 单测：stale PROCESSING 回收 + process_pending_inbox 计数修正。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.webhook_inbox import (
    STALE_PROCESSING_SEC,
    reclaim_stale_inbox,
    claim_inbox_item,
    process_pending_inbox,
)


@pytest.mark.asyncio
async def test_reclaim_stale_inbox_issues_update_and_commits():
    """reclaim 应对 PROCESSING 行发出 UPDATE（status + locked_at 条件）并 commit。"""
    db = MagicMock()
    terminal = MagicMock()
    terminal.rowcount = 0
    retry = MagicMock()
    retry.rowcount = 2
    db.execute = AsyncMock(side_effect=[terminal, retry])
    db.commit = AsyncMock()
    n = await reclaim_stale_inbox(db, stale_after=300)
    assert n == 2
    assert db.commit.await_count == 1
    assert db.execute.await_count == 2
    for call in db.execute.await_args_list:
        stmt = call.args[0]
        stmt_str = str(stmt).lower()
        assert "webhook_inbox" in stmt_str
        assert "status" in stmt_str
        assert "locked_at" in stmt_str
        compiled = stmt.compile()
        assert "PROCESSING" in [str(v) for v in compiled.params.values()]


@pytest.mark.asyncio
async def test_reclaim_stale_inbox_dead_letters_exhausted_attempts():
    db = MagicMock()
    terminal = MagicMock()
    terminal.rowcount = 1
    retry = MagicMock()
    retry.rowcount = 0
    db.execute = AsyncMock(side_effect=[terminal, retry])
    db.commit = AsyncMock()

    n = await reclaim_stale_inbox(db, stale_after=300)

    assert n == 1
    assert db.execute.await_count == 2
    terminal_stmt = db.execute.await_args_list[0].args[0]
    terminal_sql = str(terminal_stmt).lower()
    assert "attempt_count" in terminal_sql
    assert "failed" in [str(v).lower() for v in terminal_stmt.compile().params.values()]
    retry_stmt = db.execute.await_args_list[1].args[0]
    assert "attempt_count" in str(retry_stmt).lower()


@pytest.mark.asyncio
async def test_reclaim_stale_inbox_zero_when_nothing_stale():
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 0
    db.execute = AsyncMock(side_effect=[result, result])
    db.commit = AsyncMock()
    n = await reclaim_stale_inbox(db)
    assert n == 0


@pytest.mark.asyncio
async def test_reclaim_stale_inbox_uses_default_threshold():
    """默认 stale_after 应为 STALE_PROCESSING_SEC。"""
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 0
    db.execute = AsyncMock(side_effect=[result, result])
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


@pytest.mark.asyncio
async def test_webhook_inbox_claim_uses_unique_per_attempt_owner_token():
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 1
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    fetched = MagicMock(id=17, status="PROCESSING")
    db.get = AsyncMock(return_value=fetched)

    await claim_inbox_item(db, inbox_id=17, worker="same-worker")
    stmt = db.execute.await_args.args[0]
    compiled = stmt.compile()
    owner_values = [str(v) for v in compiled.params.values()]
    assert any(
        v.startswith("same-worker:") and 32 < len(v) <= 64
        for v in owner_values
    )
    assert "attempt_count" in str(stmt).lower()
    assert 8 in [v for v in compiled.params.values() if isinstance(v, int)]


@pytest.mark.asyncio
async def test_webhook_claim_lease_guard_cancels_owner_when_claim_is_lost(monkeypatch):
    import asyncio
    from app.models import WebhookInbox
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
            WebhookInbox, 1, "owner-token", interval_seconds=1
        ):
            await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    await asyncio.sleep(1.1)
    with pytest.raises(asyncio.CancelledError):
        await task



def test_deferred_webhook_payload_does_not_persist_platform_secrets_or_customer_pii():
    from app.services.webhook_inbox import _build_deferred_outbox_payload

    payload = _build_deferred_outbox_payload(
        {
            "action": "reply_urge",
            "platform": "MEITUAN",
            "platform_order_id": "platform-order-1",
            "credentials": {
                "store_id": 9,
                "access_token": "secret-access-token",
                "refresh_token": "secret-refresh-token",
            },
            "raw": {
                "reply_type": 2,
                "recipient_phone": "13800000000",
                "recipient_address": "private address",
            },
        },
        "MEITUAN",
    )
    assert payload["store_id"] == 9
    assert payload["raw"] == {"reply_type": 2}
    serialized = str(payload)
    assert "secret-access-token" not in serialized
    assert "secret-refresh-token" not in serialized
    assert "13800000000" not in serialized
    assert "private address" not in serialized


def test_deferred_webhook_payload_rejects_unknown_action():
    from app.services.webhook_inbox import _build_deferred_outbox_payload

    with pytest.raises(ValueError, match="unsupported deferred webhook action"):
        _build_deferred_outbox_payload({"action": "execute_arbitrary"}, "MEITUAN")


@pytest.mark.asyncio
async def test_enqueue_webhook_adds_row_inside_savepoint_before_flush():
    """Inbox 唯一键冲突必须发生在 SAVEPOINT 内，不能污染调用方外层事务。"""
    from app.services.webhook_inbox import enqueue_delivery_webhook

    events = []

    class Nested:
        async def __aenter__(self):
            events.append("savepoint_enter")

        async def __aexit__(self, exc_type, exc, tb):
            events.append("savepoint_exit")
            return False

    db = MagicMock()
    db.begin_nested = MagicMock(return_value=Nested())
    db.add = MagicMock(side_effect=lambda row: events.append("add"))
    db.flush = AsyncMock(side_effect=lambda: events.append("flush"))

    row, is_new = await enqueue_delivery_webhook(
        db,
        platform="MEITUAN",
        headers={"Content-Type": "application/json"},
        body=b'{"request_id":"evt-savepoint-1"}',
        raw={"request_id": "evt-savepoint-1"},
    )

    assert is_new is True
    assert row is not None
    assert events == ["savepoint_enter", "add", "flush", "savepoint_exit"]

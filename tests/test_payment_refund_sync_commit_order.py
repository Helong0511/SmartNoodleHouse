"""退款主动查单必须先提交数据库，再发送实时通知。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import payments as payment_api


@pytest.mark.asyncio
async def test_sync_refund_commits_before_websocket_notification(monkeypatch):
    events = []
    row = SimpleNamespace(
        id=7,
        refund_no="RF-7",
        order_id=9,
        amount="12.00",
        status="PROCESSING",
    )
    order = SimpleNamespace(id=9, order_no="OD-9", store_id=3)

    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    async def commit():
        events.append("commit")

    async def sync_refund_status(_db, refund):
        refund.status = "SUCCESS"
        events.append("sync")
        return refund

    async def notify(*_args, **_kwargs):
        events.append("notify")

    db.commit = AsyncMock(side_effect=commit)
    db.get = AsyncMock(return_value=order)
    monkeypatch.setattr(payment_api.PaymentService, "sync_refund_status", AsyncMock(side_effect=sync_refund_status))
    monkeypatch.setattr(payment_api, "assert_order_store_access", lambda *_: None)
    monkeypatch.setattr(payment_api.ws_manager, "notify_order_event", notify)
    monkeypatch.setattr(payment_api, "success", lambda data, request=None: data)

    response = await payment_api.sync_refund(
        "RF-7",
        request=MagicMock(),
        db=db,
        user=SimpleNamespace(id=1),
    )

    assert events == ["sync", "commit", "notify"]
    assert response["status"] == "SUCCESS"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_refund_websocket_failure_does_not_undo_committed_refund(monkeypatch):
    events = []
    row = SimpleNamespace(
        id=8,
        refund_no="RF-8",
        order_id=10,
        amount="5.00",
        status="PROCESSING",
    )
    order = SimpleNamespace(id=10, order_no="OD-10", store_id=4)
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.get = AsyncMock(return_value=order)

    async def commit():
        events.append("commit")

    async def sync_refund_status(_db, refund):
        refund.status = "SUCCESS"
        return refund

    async def notify(*_args, **_kwargs):
        events.append("notify")
        raise RuntimeError("websocket unavailable")

    db.commit = AsyncMock(side_effect=commit)
    monkeypatch.setattr(payment_api.PaymentService, "sync_refund_status", sync_refund_status)
    monkeypatch.setattr(payment_api, "assert_order_store_access", lambda *_: None)
    monkeypatch.setattr(payment_api.ws_manager, "notify_order_event", notify)
    monkeypatch.setattr(payment_api, "success", lambda data, request=None: data)

    response = await payment_api.sync_refund(
        "RF-8",
        request=MagicMock(),
        db=db,
        user=SimpleNamespace(id=1),
    )

    assert events == ["commit", "notify"]
    assert response["status"] == "SUCCESS"
    db.commit.assert_awaited_once()

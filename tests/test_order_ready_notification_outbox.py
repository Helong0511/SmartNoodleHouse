"""Regression tests for durable order-ready subscription notifications."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_order_ready_notification_is_enqueued_in_calling_transaction(monkeypatch):
    from app.services import order_lifecycle, outbox_worker

    enqueue = AsyncMock(return_value=(MagicMock(), True))
    monkeypatch.setattr(outbox_worker, "enqueue_outbox", enqueue)
    db = object()
    order = SimpleNamespace(id=321, customer_id=77)

    await order_lifecycle.notify_order_ready_subscribe(db, order)

    enqueue.assert_awaited_once_with(
        db,
        aggregate_type="order_notification",
        aggregate_id=321,
        event_kind="order_ready",
        payload={"order_id": 321},
    )


@pytest.mark.asyncio
async def test_order_ready_handler_skips_notification_if_order_is_no_longer_ready():
    from app.services.paid_side_effects import _handle_order_ready_notification

    db = MagicMock()
    db.get = AsyncMock(return_value=SimpleNamespace(
        id=321, customer_id=77, status="COMPLETED", table_id=None, order_no="A321"
    ))
    row = SimpleNamespace(aggregate_id=321)

    await _handle_order_ready_notification(db, row)

    db.get.assert_awaited_once()
    # No recipient lookup or provider call is possible for a stale READY event.


@pytest.mark.asyncio
async def test_order_ready_handler_sends_to_both_configured_mini_program_identities(monkeypatch):
    from app.services import alipay_mini_client, wechat_mini_client
    from app.services.customer_service import CustomerService
    from app.services.paid_side_effects import _handle_order_ready_notification

    db = MagicMock()
    db.get = AsyncMock(return_value=SimpleNamespace(
        id=321, customer_id=77, status="READY", table_id=None, order_no="A321"
    ))
    monkeypatch.setattr(
        CustomerService, "resolve_wechat_openid", AsyncMock(return_value="wx-openid")
    )
    monkeypatch.setattr(
        CustomerService, "resolve_alipay_user_id", AsyncMock(return_value="ali-user")
    )
    wx_send = AsyncMock(return_value=True)
    ali_send = AsyncMock(return_value=True)
    monkeypatch.setattr(wechat_mini_client, "send_order_ready_subscribe", wx_send)
    monkeypatch.setattr(alipay_mini_client, "send_order_ready_subscribe", ali_send)

    await _handle_order_ready_notification(db, SimpleNamespace(aggregate_id=321))

    wx_send.assert_awaited_once_with(
        openid="wx-openid", order_no="A321", order_id=321, table_label=None
    )
    ali_send.assert_awaited_once_with(
        alipay_user_id="ali-user", order_no="A321", order_id=321, table_label=None
    )


def test_order_ready_outbox_is_treated_as_external_side_effect():
    from app.services.outbox_worker import _is_external_side_effect_outbox

    assert _is_external_side_effect_outbox(SimpleNamespace(
        aggregate_type="order_notification", event_kind="order_ready", payload="{}"
    ))
    assert not _is_external_side_effect_outbox(SimpleNamespace(
        aggregate_type="order_notification", event_kind="other", payload="{}"
    ))


@pytest.mark.asyncio
async def test_stale_order_ready_notification_is_reclaimed_as_uncertain_not_replayed():
    from app.services.outbox_worker import reclaim_stale_processing

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    db.commit = AsyncMock()

    await reclaim_stale_processing(db, stale_after=300)

    statement = db.execute.await_args.args[0]
    compiled = statement.compile()
    values = [str(value) for value in compiled.params.values()]
    assert "order_notification" in values
    assert "UNCERTAIN" in values
    assert "EXTERNAL_RESULT_UNKNOWN" in values

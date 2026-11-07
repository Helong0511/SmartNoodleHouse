"""Regression tests for durable order-ready subscription notifications."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_order_ready_notifications_are_enqueued_per_channel_in_calling_transaction(monkeypatch):
    from app.services import order_lifecycle, outbox_worker

    enqueue = AsyncMock(return_value=(MagicMock(), True))
    monkeypatch.setattr(outbox_worker, "enqueue_outbox", enqueue)
    db = object()
    order = SimpleNamespace(id=321, customer_id=77)

    await order_lifecycle.notify_order_ready_subscribe(db, order)

    assert enqueue.await_count == 2
    assert [call.kwargs["event_kind"] for call in enqueue.await_args_list] == [
        "order_ready_wechat", "order_ready_alipay"
    ]
    assert all(call.kwargs["aggregate_type"] == "order_notification" for call in enqueue.await_args_list)
    assert all(call.args[0] is db for call in enqueue.await_args_list)
    assert [call.kwargs["payload"]["channel"] for call in enqueue.await_args_list] == [
        "wechat", "alipay"
    ]


@pytest.mark.asyncio
async def test_order_ready_handler_skips_notification_if_order_is_no_longer_ready():
    from app.services.paid_side_effects import _handle_order_ready_notification

    db = MagicMock()
    db.get = AsyncMock(return_value=SimpleNamespace(
        id=321, customer_id=77, status="COMPLETED", table_id=None, order_no="A321"
    ))

    await _handle_order_ready_notification(
        db, SimpleNamespace(aggregate_id=321, event_kind="order_ready_wechat")
    )

    db.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_order_ready_wechat_handler_sends_only_wechat(monkeypatch):
    from app.services import wechat_mini_client
    from app.services.customer_service import CustomerService
    from app.services.paid_side_effects import _handle_order_ready_notification

    db = MagicMock()
    db.get = AsyncMock(return_value=SimpleNamespace(
        id=321, customer_id=77, status="READY", table_id=None, order_no="A321"
    ))
    monkeypatch.setattr(
        CustomerService, "resolve_wechat_openid", AsyncMock(return_value="wx-openid")
    )
    wx_send = AsyncMock(return_value=True)
    monkeypatch.setattr(wechat_mini_client, "send_order_ready_subscribe", wx_send)

    await _handle_order_ready_notification(
        db, SimpleNamespace(aggregate_id=321, event_kind="order_ready_wechat")
    )

    wx_send.assert_awaited_once_with(
        openid="wx-openid", order_no="A321", order_id=321, table_label=None
    )


@pytest.mark.asyncio
async def test_order_ready_alipay_handler_sends_only_alipay(monkeypatch):
    from app.services import alipay_mini_client
    from app.services.customer_service import CustomerService
    from app.services.paid_side_effects import _handle_order_ready_notification

    db = MagicMock()
    db.get = AsyncMock(return_value=SimpleNamespace(
        id=321, customer_id=77, status="READY", table_id=None, order_no="A321"
    ))
    monkeypatch.setattr(
        CustomerService, "resolve_alipay_user_id", AsyncMock(return_value="ali-user")
    )
    ali_send = AsyncMock(return_value=True)
    monkeypatch.setattr(alipay_mini_client, "send_order_ready_subscribe", ali_send)

    await _handle_order_ready_notification(
        db, SimpleNamespace(aggregate_id=321, event_kind="order_ready_alipay")
    )

    ali_send.assert_awaited_once_with(
        alipay_user_id="ali-user", order_no="A321", order_id=321, table_label=None
    )


@pytest.mark.parametrize("event_kind", ["order_ready_wechat", "order_ready_alipay"])
def test_order_ready_outbox_is_treated_as_external_side_effect(event_kind):
    from app.services.outbox_worker import _is_external_side_effect_outbox

    assert _is_external_side_effect_outbox(SimpleNamespace(
        aggregate_type="order_notification", event_kind=event_kind, payload="{}"
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

"""支付宝小程序订阅消息：出餐 / 余额偏低发送路径。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import alipay_mini_client as alipay_mini
from app.services.system_settings_service import SystemSettingsService


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    SystemSettingsService.invalidate_cache_local()
    SystemSettingsService._last_db_cache = None
    yield
    SystemSettingsService.invalidate_cache_local()
    SystemSettingsService._last_db_cache = None


@pytest.mark.asyncio
async def test_alipay_order_ready_skips_without_template():
    SystemSettingsService._last_db_cache = {"alipay_subscribe_template_order_ready": ""}
    ok = await alipay_mini.send_order_ready_subscribe(
        alipay_user_id="2088xxx",
        order_no="ORD1",
        order_id=1,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_alipay_order_ready_mock_when_unconfigured(monkeypatch):
    SystemSettingsService._last_db_cache = {
        "alipay_subscribe_template_order_ready": "TPL_ALIPAY_READY",
    }
    monkeypatch.setattr(alipay_mini, "_alipay_mini_configured", lambda: False)
    monkeypatch.setattr(alipay_mini, "_mock_allowed", lambda: True)
    ok = await alipay_mini.send_order_ready_subscribe(
        alipay_user_id="2088102122458832",
        order_no="ORD2",
        order_id=2,
        table_label="A1",
    )
    assert ok is True


@pytest.mark.asyncio
async def test_alipay_order_ready_calls_gateway(monkeypatch):
    SystemSettingsService._last_db_cache = {
        "alipay_subscribe_template_order_ready": "TPL_ALIPAY_READY",
    }
    monkeypatch.setattr(alipay_mini, "_alipay_mini_configured", lambda: True)
    gateway = AsyncMock(return_value={"code": "10000", "msg": "Success"})
    monkeypatch.setattr(alipay_mini, "_templatemessage_gateway", gateway)
    ok = await alipay_mini.send_order_ready_subscribe(
        alipay_user_id="2088102122458832",
        order_no="ORD3",
        order_id=3,
        table_label="窗边",
    )
    assert ok is True
    assert gateway.await_count == 1
    biz = gateway.await_args.args[0]
    assert biz["to_user_id"] == "2088102122458832"
    assert biz["user_template_id"] == "TPL_ALIPAY_READY"
    assert "form_id" not in biz
    assert "order_id=3" in biz["page"]


@pytest.mark.asyncio
async def test_alipay_balance_low_gateway_fail(monkeypatch):
    SystemSettingsService._last_db_cache = {
        "alipay_subscribe_template_balance_low": "TPL_BAL",
    }
    monkeypatch.setattr(alipay_mini, "_alipay_mini_configured", lambda: True)
    monkeypatch.setattr(
        alipay_mini,
        "_templatemessage_gateway",
        AsyncMock(return_value={"code": "40004", "sub_msg": "MSG_UNSUBSCRIBED"}),
    )
    ok = await alipay_mini.send_balance_low_subscribe(
        alipay_user_id="2088u",
        customer_id=9,
        balance="3.00",
        threshold="50.00",
    )
    assert ok is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("order_channel", "expected_event"),
    [("WECHAT", "order_ready_wechat"), ("ALIPAY", "order_ready_alipay")],
)
async def test_notify_order_ready_enqueues_only_order_origin_platform(
    db_session, monkeypatch, order_channel, expected_event
):
    from app.models import Customer, Order
    from app.services import order_lifecycle, outbox_worker

    c = Customer(
        id=8801, customer_no="CU_SUB_CHANNEL", nickname="平台用户", member_status="MEMBER"
    )
    db_session.add(c)
    await db_session.flush()
    order = Order(
        id=88001,
        order_no="SNHSUB0001",
        store_id=1,
        customer_id=c.id,
        status="COOKING",
        channel=order_channel,
        dining_type="DINE_IN",
        subtotal=10,
        total_amount=10,
    )
    db_session.add(order)
    await db_session.commit()

    enqueue = AsyncMock(return_value=(None, True))
    monkeypatch.setattr(outbox_worker, "enqueue_outbox", enqueue)

    await order_lifecycle.notify_order_ready_subscribe(db_session, order)

    assert enqueue.await_count == 1
    assert enqueue.await_args.kwargs["event_kind"] == expected_event
    assert enqueue.await_args.kwargs["aggregate_id"] == order.id


@pytest.mark.asyncio
@pytest.mark.parametrize("order_channel", ["H5", "POS", "MEITUAN", "ELEME", None])
async def test_notify_order_ready_skips_non_miniprogram_channels(
    db_session, monkeypatch, order_channel
):
    from app.models import Customer, Order
    from app.services import order_lifecycle, outbox_worker

    c = Customer(
        id=8802, customer_no="CU_SUB_NON_MINI", nickname="非小程序用户", member_status="MEMBER"
    )
    db_session.add(c)
    await db_session.flush()
    order = Order(
        id=88002,
        order_no="SNHSUB0002",
        store_id=1,
        customer_id=c.id,
        status="COOKING",
        channel=order_channel,
        dining_type="DINE_IN",
        subtotal=10,
        total_amount=10,
    )
    db_session.add(order)
    await db_session.commit()

    enqueue = AsyncMock(return_value=(None, True))
    monkeypatch.setattr(outbox_worker, "enqueue_outbox", enqueue)

    await order_lifecycle.notify_order_ready_subscribe(db_session, order)

    enqueue.assert_not_awaited()

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
async def test_notify_order_ready_tries_both_channels(db_session, monkeypatch):
    from app.models import Customer, Order
    from app.services import order_lifecycle
    from app.services.customer_service import CustomerService

    c = Customer(id=8801, customer_no="CU_SUB_BOTH", nickname="双端", member_status="MEMBER")
    db_session.add(c)
    await db_session.flush()
    order = Order(
        id=88001,
        order_no="SNHSUB0001",
        store_id=1,
        customer_id=c.id,
        status="COOKING",
        channel="ALIPAY",
        dining_type="DINE_IN",
        subtotal=10,
        total_amount=10,
    )
    db_session.add(order)
    await db_session.commit()

    monkeypatch.setattr(
        CustomerService, "resolve_wechat_openid", AsyncMock(return_value="oxxx")
    )
    monkeypatch.setattr(
        CustomerService, "resolve_alipay_user_id", AsyncMock(return_value="2088yyy")
    )
    wx = AsyncMock(return_value=True)
    ali = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.services.wechat_mini_client.send_order_ready_subscribe", wx
    )
    monkeypatch.setattr(
        "app.services.alipay_mini_client.send_order_ready_subscribe", ali
    )

    await order_lifecycle.notify_order_ready_subscribe(db_session, order)
    # 通知投递有意在后台 task 中执行，避免延长订单行锁；让事件循环调度任务后再断言。
    import asyncio
    await asyncio.sleep(0)
    # 投递通过 create_task 异步调度；让两个任务获得一次执行机会后再断言。
    assert wx.await_count == 1
    assert ali.await_count == 1

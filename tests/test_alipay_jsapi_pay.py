"""支付宝小程序 JSAPI 创单（alipay.trade.create）。"""
from decimal import Decimal

import pytest

from app.core.exceptions import AppException
from app.services import alipay_client


@pytest.mark.asyncio
async def test_trade_create_builds_jsapi_payload(monkeypatch):
    captured: dict = {}

    async def fake_call(method, biz_content, *, allow_codes=None):
        captured["method"] = method
        captured["biz"] = biz_content
        return {"code": "10000", "trade_no": "2024090322001000000001"}

    monkeypatch.setattr(alipay_client, "_gateway_call", fake_call)

    data = await alipay_client.trade_create(
        payment_no="PAY20240903001",
        amount_yuan=Decimal("12.50"),
        subject="订单ORD1",
        buyer_id="2088123456789012",
        op_app_id="2021006196676140",
    )
    assert captured["method"] == "alipay.trade.create"
    assert captured["biz"]["product_code"] == "JSAPI_PAY"
    assert captured["biz"]["buyer_id"] == "2088123456789012"
    assert captured["biz"]["op_app_id"] == "2021006196676140"
    assert data["trade_no"] == "2024090322001000000001"


@pytest.mark.asyncio
async def test_trade_create_requires_buyer_id():
    with pytest.raises(AppException) as ei:
        await alipay_client.trade_create(
            payment_no="PAY1",
            amount_yuan=Decimal("1.00"),
            subject="x",
            buyer_id="",
        )
    assert ei.value.code == 40042


def test_friendly_alipay_permission_error():
    msg = alipay_client._friendly_alipay_error(
        "alipay.trade.create",
        {
            "code": "40004",
            "sub_code": "ACQ.ACCESS_FORBIDDEN",
            "sub_msg": "接口调用权限不足，解决方案：https://open.alipay.com/api/errCheck",
        },
    )
    assert "JSAPI支付" in msg
    assert "当面付" in msg


@pytest.mark.asyncio
async def test_alipay_provider_mini_uses_trade_create(monkeypatch):
    from app.services.payment_service import AlipayProvider

    called = {}

    async def fake_trade_create(**kwargs):
        called.update(kwargs)
        return {
            "provider": "ALIPAY",
            "payment_no": kwargs["payment_no"],
            "trade_no": "TNO1",
            "amount": "1.00",
        }

    monkeypatch.setattr(
        "app.services.payment_service._alipay_pay_configured",
        lambda: True,
    )
    monkeypatch.setattr(alipay_client, "trade_create", fake_trade_create)
    monkeypatch.setattr(
        "app.services.payment_service.alipay_client.trade_create",
        fake_trade_create,
    )

    payment = type(
        "P",
        (),
        {
            "payment_no": "PAYX",
            "channel": "ALIPAY_MINIAPP",
            "amount": Decimal("1.00"),
        },
    )()
    order = type("O", (), {"order_no": "ORDX"})()
    data = await AlipayProvider().create_payment(
        payment, order, extra={"buyer_id": "2088000000000001"}
    )
    assert data["trade_no"] == "TNO1"
    assert called["buyer_id"] == "2088000000000001"
    assert called["payment_no"] == "PAYX"
    assert called["subject"] == "订单ORDX"

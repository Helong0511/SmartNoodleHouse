"""付款码轮询逻辑（mock 外部 API）。"""
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.barcode_pay_service import settle_alipay_barcode, settle_wechat_barcode


@pytest.mark.asyncio
async def test_wechat_micropay_success_immediate():
    with patch(
        "app.services.barcode_pay_service.wechat_pay_v2_client.micropay",
        new_callable=AsyncMock,
        return_value={"trade_state": "SUCCESS", "transaction_id": "wx123"},
    ):
        data = await settle_wechat_barcode(
            payment_no="P1",
            description="测试",
            amount_yuan=Decimal("1.00"),
            auth_code="101234567890123456",
        )
    assert data["immediate_paid"] is True
    assert data["provider_trade_no"] == "wx123"


@pytest.mark.asyncio
async def test_wechat_userpaying_then_query_success():
    with patch(
        "app.services.barcode_pay_service.wechat_pay_v2_client.micropay",
        new_callable=AsyncMock,
        return_value={"trade_state": "USERPAYING"},
    ), patch(
        "app.services.barcode_pay_service.wechat_pay_v2_client.order_query",
        new_callable=AsyncMock,
        return_value={"trade_state": "SUCCESS", "transaction_id": "wx456"},
    ), patch("app.services.barcode_pay_service.asyncio.sleep", new_callable=AsyncMock):
        data = await settle_wechat_barcode(
            payment_no="P2",
            description="测试",
            amount_yuan=Decimal("2.00"),
            auth_code="111234567890123456",
        )
    assert data["immediate_paid"] is True


@pytest.mark.asyncio
async def test_alipay_wait_buyer_pay_then_query():
    with patch(
        "app.services.barcode_pay_service.alipay_client.trade_bar_pay",
        new_callable=AsyncMock,
        return_value={"waiting_payment": True, "trade_status": "WAIT_BUYER_PAY"},
    ), patch(
        "app.services.barcode_pay_service.alipay_client.trade_query",
        new_callable=AsyncMock,
        return_value={"trade_status": "TRADE_SUCCESS", "trade_no": "ali789"},
    ), patch("app.services.barcode_pay_service.asyncio.sleep", new_callable=AsyncMock):
        data = await settle_alipay_barcode(
            payment_no="P3",
            amount_yuan=Decimal("3.00"),
            subject="订单",
            auth_code="2812345678901234",
        )
    assert data["immediate_paid"] is True
    assert data["provider_trade_no"] == "ali789"


@pytest.mark.asyncio
async def test_wechat_poll_timeout():
    with patch(
        "app.services.barcode_pay_service.wechat_pay_v2_client.micropay",
        new_callable=AsyncMock,
        return_value={"trade_state": "USERPAYING"},
    ), patch(
        "app.services.barcode_pay_service.wechat_pay_v2_client.order_query",
        new_callable=AsyncMock,
        return_value={"trade_state": "USERPAYING"},
    ), patch("app.services.barcode_pay_service.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(AppException) as exc:
            await settle_wechat_barcode(
                payment_no="P4",
                description="测试",
                amount_yuan=Decimal("1.00"),
                auth_code="121234567890123456",
            )
    assert exc.value.code == 50013

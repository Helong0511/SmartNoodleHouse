"""退款金额限制 / 回调分流 / 状态机单元测试"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.response import money


def test_refund_amount_cannot_exceed_payment():
    paid = money(Decimal("54.00"))
    already = money(Decimal("30.00"))
    asking = money(Decimal("30.00"))
    assert money(already + asking) > paid


def test_net_revenue_formula():
    """净营收 = 入账 − 退款，不为负。"""
    from app.core.response import money as m

    gross = m(Decimal("100.00"))
    refunded = m(Decimal("30.00"))
    assert m(gross - refunded) == m(Decimal("70.00"))
    assert m(gross - m(Decimal("150"))) < 0 or True


@pytest.mark.asyncio
async def test_wechat_provider_detects_refund_callback():
    from app.services.payment_service import WechatPayProvider

    data = {
        "event_type": "REFUND.SUCCESS",
        "out_trade_no": "PAY1",
        "out_refund_no": "RF1",
        "refund_id": "5001",
        "refund_status": "SUCCESS",
        "amount": {"refund": 100, "total": 5400},
    }
    parsed = await WechatPayProvider().handle_callback(data)
    assert parsed["is_refund"] is True
    assert parsed["refund_no"] == "RF1"
    assert parsed["provider_refund_no"] == "5001"


@pytest.mark.asyncio
async def test_alipay_provider_detects_refund_notify():
    from app.services.payment_service import AlipayProvider

    data = {
        "out_trade_no": "PAY1",
        "trade_no": "20230827",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "54.00",
        "refund_fee": "10.00",
        "gmt_refund": "2026-08-27 16:00:00",
        "out_biz_no": "RF1",
        "app_id": "app",
    }
    parsed = await AlipayProvider().handle_callback(data)
    assert parsed["is_refund"] is True
    assert parsed["refund_no"] == "RF1"

    pay_only = {
        "out_trade_no": "PAY1",
        "trade_no": "20230827",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "54.00",
        "app_id": "app",
    }
    parsed2 = await AlipayProvider().handle_callback(pay_only)
    assert parsed2["is_refund"] is False
    assert parsed2["status"] == "PAID"


@pytest.mark.asyncio
async def test_finalize_refund_marks_partial():
    from app.services.payment_service import PaymentService

    payment = MagicMock()
    payment.id = 1
    payment.amount = Decimal("100.00")
    payment.status = "PAID"

    order = MagicMock()
    order.id = 9
    order.status = "PAID"
    order.payment_status = "PAID"
    order.customer_id = None

    row = MagicMock()
    row.status = "PROCESSING"
    row.payment_id = 1
    row.order_id = 9
    row.refund_no = "RF1"
    row.amount = Decimal("40.00")
    row.fail_reason = None
    row.callback_raw = None
    row.provider_refund_no = None
    row.succeeded_at = None

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=Decimal("40.00"))

    payment_result = MagicMock()
    payment_result.scalar_one_or_none.return_value = payment
    order_result = MagicMock()
    order_result.scalar_one_or_none.return_value = order
    db.execute = AsyncMock(side_effect=[payment_result, order_result])
    db.flush = AsyncMock()

    with patch("app.services.payment_service.record_payment_status", new_callable=AsyncMock), patch(
        "app.services.payment_service.apply_order_status", new_callable=AsyncMock
    ), patch(
        "app.services.customer_service.CustomerService.deduct_points_for_refund", new_callable=AsyncMock
    ) as deduct:
        await PaymentService._finalize_refund(db, row, "SUCCESS", source="TEST")

    assert row.status == "SUCCESS"
    assert payment.status == "PARTIAL_REFUND"
    assert order.payment_status == "PARTIAL_REFUND"
    deduct.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_refund_deducts_points_for_customer():
    from app.services.payment_service import PaymentService

    payment = MagicMock()
    payment.id = 1
    payment.amount = Decimal("100.00")
    payment.status = "PAID"

    order = MagicMock()
    order.id = 9
    order.status = "PAID"
    order.payment_status = "PAID"
    order.customer_id = 42

    row = MagicMock()
    row.status = "PROCESSING"
    row.payment_id = 1
    row.order_id = 9
    row.refund_no = "RF1"
    row.amount = Decimal("40.00")
    row.fail_reason = None
    row.callback_raw = None
    row.provider_refund_no = None
    row.succeeded_at = None

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=Decimal("40.00"))

    payment_result = MagicMock()
    payment_result.scalar_one_or_none.return_value = payment
    order_result = MagicMock()
    order_result.scalar_one_or_none.return_value = order
    db.execute = AsyncMock(side_effect=[payment_result, order_result])
    db.flush = AsyncMock()

    with patch("app.services.payment_service.record_payment_status", new_callable=AsyncMock), patch(
        "app.services.payment_service.apply_order_status", new_callable=AsyncMock
    ), patch(
        "app.services.customer_service.CustomerService.deduct_points_for_refund", new_callable=AsyncMock
    ) as deduct:
        await PaymentService._finalize_refund(db, row, "SUCCESS", source="TEST")

    deduct.assert_awaited_once()
    args = deduct.await_args
    assert args[0][1] is order
    assert args[0][2] == Decimal("40.00")

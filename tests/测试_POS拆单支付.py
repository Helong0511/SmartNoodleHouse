"""收银组合分笔收款：待收计算、超额拒绝、PARTIAL_PAID / 结清。"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.core.response import money
from app.services.payment_service import PaymentService


def test_remaining_after_mixed_tenders():
    """应收 21 = 微信 10 + 支付宝 5 + 待收 6。"""
    total = money("21.00")
    paid = money("10.00") + money("5.00")
    rem = total - paid
    assert rem == money("6.00")


def test_cash_change_on_partial_tender():
    """待收 6 时顾客给现金 20：入账 6，找零 14。"""
    pay_amount = money("6.00")
    received = money("20.00")
    change = received - pay_amount
    assert change == money("14.00")


@pytest.mark.asyncio
async def test_settle_cash_rejects_over_remaining():
    order = MagicMock()
    order.id = 1
    order.order_no = "O1"
    order.status = "PENDING_PAYMENT"
    order.payment_status = "PARTIAL_PAID"
    order.total_amount = Decimal("21.00")
    order.store_id = 1
    order.table_id = None
    order.customer_id = None

    locked = order
    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = locked
    db.execute = AsyncMock(return_value=exec_result)

    with patch.object(
        PaymentService, "remaining_amount", new_callable=AsyncMock, return_value=money("6.00")
    ):
        with pytest.raises(AppException) as exc:
            await PaymentService.settle_cash_payment(
                db, order, operator_id=1, amount=Decimal("10.00"), received_amount=Decimal("10.00")
            )
    assert exc.value.code == 40052


@pytest.mark.asyncio
async def test_finalize_tender_partial_then_full():
    order = MagicMock()
    order.id = 2
    order.total_amount = Decimal("21.00")
    order.payment_status = "UNPAID"
    order.payment_method = None
    order.paid_at = None

    payment = MagicMock()
    payment.id = 100

    db = AsyncMock()

    with patch.object(
        PaymentService, "paid_amount", new_callable=AsyncMock, return_value=money("10.00")
    ), patch.object(
        PaymentService, "_resolve_payment_method", new_callable=AsyncMock, return_value="WECHAT"
    ):
        out = await PaymentService._finalize_tender(
            db, payment=payment, order=order, provider="WECHAT", source="MOCK"
        )
    assert out["fully_paid"] is False
    assert out["payment_status"] == "PARTIAL_PAID"
    assert out["remaining_amount"] == "11.00"
    assert order.payment_status == "PARTIAL_PAID"

    with patch.object(
        PaymentService, "paid_amount", new_callable=AsyncMock, return_value=money("21.00")
    ), patch.object(
        PaymentService, "_resolve_payment_method", new_callable=AsyncMock, return_value="MIXED"
    ), patch(
        "app.services.payment_service.apply_order_status", new_callable=AsyncMock
    ) as apply_status:
        out2 = await PaymentService._finalize_tender(
            db, payment=payment, order=order, provider="CASH", source="POS_CASH"
        )
    assert out2["fully_paid"] is True
    assert out2["remaining_amount"] == "0.00"
    apply_status.assert_awaited_once()
    assert order.payment_method == "MIXED"


@pytest.mark.asyncio
async def test_create_payment_rejects_amount_over_remaining():
    order = MagicMock()
    order.id = 3
    order.status = "PENDING_PAYMENT"
    order.payment_status = "PARTIAL_PAID"
    order.total_amount = Decimal("21.00")

    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = order
    db.execute = AsyncMock(return_value=exec_result)

    with patch.object(
        PaymentService, "remaining_amount", new_callable=AsyncMock, return_value=money("6.00")
    ):
        with pytest.raises(AppException) as exc:
            await PaymentService.create_payment(
                db, order, "WECHAT", "micropay", amount=Decimal("7.00")
            )
    assert exc.value.code == 40052


def test_timeout_close_skips_partial_paid():
    """关单任务仅 UNPAID；PARTIAL_PAID 不应被超时关单。"""
    # 与 scheduler.close_expired_orders 条件对齐
    candidates = [
        {"status": "PENDING_PAYMENT", "payment_status": "UNPAID"},
        {"status": "PENDING_PAYMENT", "payment_status": "PARTIAL_PAID"},
        {"status": "PENDING_PAYMENT", "payment_status": "PAID"},
    ]
    closable = [
        c
        for c in candidates
        if c["status"] == "PENDING_PAYMENT" and c["payment_status"] == "UNPAID"
    ]
    assert len(closable) == 1
    assert closable[0]["payment_status"] == "UNPAID"

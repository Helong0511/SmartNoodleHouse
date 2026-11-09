"""取消订单迟到支付：不复活订单，必须将逐笔原路退款持久化入队。"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.order_state_machine import assert_payment_transition
from app.services.payment_service import PaymentService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("captured", "expected_payment_status"),
    [
        (Decimal("10.00"), "PAID"),
        (Decimal("5.00"), "PARTIAL_PAID"),
    ],
)
async def test_late_payment_for_cancelled_order_queues_refund_without_reviving(
    captured, expected_payment_status
):
    order = SimpleNamespace(
        id=501,
        store_id=7,
        order_no="O-LATE-501",
        status="CANCELLED",
        payment_status="UNPAID",
        payment_method=None,
        paid_at=None,
        total_amount=Decimal("10.00"),
        channel="WECHAT",
    )
    payment = SimpleNamespace(
        id=601,
        order_id=order.id,
        payment_no="P-LATE-601",
        provider="WECHAT",
        channel="WECHAT_MINIPROGRAM",
        amount=captured,
    )
    db = SimpleNamespace(flush=AsyncMock())

    with (
        patch(
            "app.services.financial_ledger_service.FinancialLedgerService.record_sale",
            new_callable=AsyncMock,
        ) as record_sale,
        patch.object(PaymentService, "paid_amount", new=AsyncMock(return_value=captured)),
        patch.object(
            PaymentService,
            "_resolve_payment_method",
            new=AsyncMock(return_value="WECHAT"),
        ),
        patch(
            "app.services.outbox_worker.enqueue_outbox", new_callable=AsyncMock
        ) as enqueue,
        patch(
            "app.services.payment_service.apply_order_status", new_callable=AsyncMock
        ) as change_status,
    ):
        result = await PaymentService._finalize_tender(
            db,
            payment=payment,
            order=order,
            provider="WECHAT",
            source="CALLBACK",
        )

    record_sale.assert_awaited_once()
    enqueue.assert_awaited_once_with(
        db,
        aggregate_type="late_payment",
        aggregate_id=payment.id,
        event_kind="auto_refund",
        payload={
            "payment_id": payment.id,
            "order_id": order.id,
            "reason": "订单已取消，支付回调迟到成功，自动原路退款",
        },
    )
    change_status.assert_not_awaited()
    assert order.status == "CANCELLED"
    assert order.payment_status == expected_payment_status
    assert result["late_payment"] is True
    assert result["refund_queued"] is True



def test_cancelled_order_cannot_be_revived_by_payment_transition():
    with pytest.raises(AppException) as exc:
        assert_payment_transition("CANCELLED", "PAID")
    assert exc.value.code == 40024

"""分笔退款：单笔退满不得误标整单 REFUNDED。"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.response import money
from app.services.payment_service import PaymentService


@pytest.mark.asyncio
async def test_order_net_collected_sums_remaining_after_refunds():
    p1 = SimpleNamespace(id=1, amount=Decimal("50.00"), status="REFUNDED")
    p2 = SimpleNamespace(id=2, amount=Decimal("50.00"), status="PAID")
    db = MagicMock()
    pay_result = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[p1, p2]))))
    refund_result = MagicMock(all=MagicMock(return_value=[(1, Decimal("50.00"))]))
    db.execute = AsyncMock(side_effect=[pay_result, refund_result])

    net = await PaymentService.order_net_collected(db, 9)
    assert net == money("50.00")


@pytest.mark.asyncio
async def test_finalize_refund_keeps_order_when_other_tender_remains():
    row = SimpleNamespace(
        id=7,
        payment_id=1,
        order_id=9,
        amount=Decimal("50.00"),
        refund_no="R1",
        status="PROCESSING",
        provider_refund_no=None,
        callback_raw=None,
        fail_reason=None,
        succeeded_at=None,
    )
    payment = SimpleNamespace(id=1, order_id=9, amount=Decimal("50.00"), status="PAID", channel="POS", provider="CASH")
    order = SimpleNamespace(
        id=9,
        store_id=1,
        status="COOKING",
        payment_status="PAID",
        total_amount=Decimal("100.00"),
        channel="POS",
        customer_id=None,
    )

    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=order)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=payment)),
        ]
    )
    db.scalar = AsyncMock(return_value=Decimal("50.00"))
    db.flush = AsyncMock()

    with patch("app.services.payment_service.record_payment_status", AsyncMock()):
        with patch.object(PaymentService, "_refund_rows_current", AsyncMock(return_value=[row])):
            with patch.object(PaymentService, "order_net_collected", AsyncMock(return_value=money("50.00"))):
                with patch(
                    "app.services.financial_ledger_service.FinancialLedgerService.record_refund",
                    AsyncMock(return_value=None),
                ):
                    with patch("app.services.payment_service.apply_order_status", AsyncMock()) as apply, \
                         patch.object(PaymentService, "_sync_pos_refund_request_on_success", AsyncMock()):
                        out = await PaymentService._finalize_refund(db, row, "SUCCESS", source="TEST")

    assert out.status == "SUCCESS"
    assert payment.status == "REFUNDED"
    assert order.status == "COOKING"
    assert order.payment_status == "PARTIAL_REFUND"
    apply.assert_not_called()

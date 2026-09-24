"""P0 跟进：部分退款不可重复收款、调账门店隔离、退款金额 fail-closed。"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException


@pytest.mark.asyncio
async def test_remaining_after_partial_refund_uses_net_collected():
    """部分退款后 remaining 应按净已收计算，不得虚高导致重复收款。"""
    from app.services.payment_service import PaymentService

    order = SimpleNamespace(
        id=1,
        total_amount=Decimal("100.00"),
        payment_status="PARTIAL_REFUND",
        status="CONFIRMED",
    )
    with patch.object(
        PaymentService,
        "order_net_collected",
        new_callable=AsyncMock,
        return_value=Decimal("70.00"),
    ):
        rem = await PaymentService.remaining_amount(AsyncMock(), order)
    assert rem == Decimal("30.00")


@pytest.mark.asyncio
async def test_remaining_zero_when_fully_refunded():
    from app.services.payment_service import PaymentService

    order = SimpleNamespace(
        id=1,
        total_amount=Decimal("100.00"),
        payment_status="REFUNDED",
        status="REFUNDED",
    )
    rem = await PaymentService.remaining_amount(AsyncMock(), order)
    assert rem == Decimal("0.00")


@pytest.mark.asyncio
async def test_refund_callback_missing_amount_fail_closed():
    from app.services.payment_service import PaymentService

    row = SimpleNamespace(id=1, amount=Decimal("10.00"), refund_no="R1", payment_id=2, order_id=3, status="PENDING")
    locked_refund = MagicMock()
    locked_refund.scalar_one_or_none = MagicMock(return_value=row)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=locked_refund)

    with pytest.raises(AppException) as ei:
        await PaymentService._apply_refund_callback(
            db,
            "WECHAT",
            {"event_type": "REFUND.SUCCESS"},
            {
                "payment_no": "P1",
                "refund_no": "R1",
                "refund_status": "SUCCESS",
                # 故意缺 refund_amount / refund_fee
            },
            "receipt-key",
        )
    assert ei.value.code == 40302


@pytest.mark.asyncio
async def test_pos_cancel_asserts_store_access():
    from app.services.pos_refund_service import PosRefundService

    row = SimpleNamespace(id=5, store_id=2, requested_by=1, status="PENDING")
    locked = MagicMock()
    locked.scalar_one_or_none = MagicMock(return_value=row)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=locked)
    user = SimpleNamespace(id=1, store_id=1, role=SimpleNamespace(code="CASHIER"))

    with patch(
        "app.services.store_scope.assert_store_access",
        side_effect=AppException(40301, "无权访问该门店数据"),
    ):
        with pytest.raises(AppException) as ei:
            await PosRefundService.cancel_request(db, user=user, request_id=5)
    assert ei.value.code == 40301


@pytest.mark.asyncio
async def test_merge_wallet_moves_balances():
    from app.services.customer_merge_service import CustomerMergeService

    source = SimpleNamespace(
        id=2,
        balance_principal=Decimal("30.00"),
        balance_gift=Decimal("5.00"),
    )
    target = SimpleNamespace(
        id=1,
        balance_principal=Decimal("10.00"),
        balance_gift=Decimal("0.00"),
    )
    stats: dict = {}
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(rowcount=3),
            MagicMock(rowcount=1),
        ]
    )
    db.flush = AsyncMock()

    await CustomerMergeService._merge_wallet(db, source, target, stats)

    assert target.balance_principal == Decimal("40.00")
    assert target.balance_gift == Decimal("5.00")
    assert source.balance_principal == Decimal("0.00")
    assert source.balance_gift == Decimal("0.00")
    assert stats["balance_principal_added"] == "30.00"
    db.add.assert_called_once()

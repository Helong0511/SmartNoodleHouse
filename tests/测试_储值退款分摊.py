from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.balance_service import BalanceService


def _result(value=None, *, one=None):
    result = MagicMock()
    result.scalar_one.return_value = value
    result.scalar_one_or_none.return_value = value
    result.one.return_value = one
    return result


@pytest.mark.asyncio
async def test_split_balance_refunds_cumulatively_restore_exact_gift_and_principal():
    customer = SimpleNamespace(id=7, balance_principal=Decimal("0.00"), balance_gift=Decimal("0.00"))
    pay_row = SimpleNamespace(
        delta_principal=Decimal("-7.00"),
        delta_gift=Decimal("-3.00"),
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _result(customer),                         # lock customer
        _result(None),                             # refund idempotency lookup
        _result(pay_row),                           # original PAY ledger
        _result(one=(Decimal("0.00"), Decimal("0.00"))),  # prior restores
        _result(None),                             # store lookup is db.get, not execute
    ])
    db.get = AsyncMock(return_value=SimpleNamespace(store_no="S1"))
    db.add = MagicMock()
    db.flush = AsyncMock()

    first = await BalanceService.restore_for_refund(
        db, customer_id=7, order_id=90, refund_id=501,
        amount=Decimal("3.33"), store_id=1,
    )
    assert first.delta_gift == Decimal("1.00")
    assert first.delta_principal == Decimal("2.33")

    # 第二笔退款前，数据库中已有第一笔回补流水，累计赠送金目标应精确到 3.00。
    db.execute = AsyncMock(side_effect=[
        _result(customer),
        _result(None),
        _result(pay_row),
        _result(one=(Decimal("2.33"), Decimal("1.00"))),
    ])
    second = await BalanceService.restore_for_refund(
        db, customer_id=7, order_id=90, refund_id=502,
        amount=Decimal("6.67"), store_id=1,
    )

    assert second.delta_gift == Decimal("2.00")
    assert second.delta_principal == Decimal("4.67")
    assert second.delta_gift + first.delta_gift == Decimal("3.00")
    assert second.delta_principal + first.delta_principal == Decimal("7.00")


@pytest.mark.asyncio
async def test_balance_refund_cannot_restore_more_than_original_wallet_payment():
    customer = SimpleNamespace(id=7, balance_principal=Decimal("0.00"), balance_gift=Decimal("0.00"))
    pay_row = SimpleNamespace(
        delta_principal=Decimal("-7.00"),
        delta_gift=Decimal("-3.00"),
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _result(customer),
        _result(None),
        _result(pay_row),
        _result(one=(Decimal("7.00"), Decimal("3.00"))),
    ])

    from app.core.exceptions import AppException

    with pytest.raises(AppException) as exc:
        await BalanceService.restore_for_refund(
            db, customer_id=7, order_id=90, refund_id=503,
            amount=Decimal("0.01"), store_id=1,
        )

    assert exc.value.code == 40992
    db.add.assert_not_called()
    db.flush.assert_not_awaited()



@pytest.mark.asyncio
async def test_wallet_debit_idempotency_rejects_changed_amount_or_payment_reference():
    from app.core.exceptions import AppException

    customer = SimpleNamespace(id=7, balance_principal=Decimal("20.00"), balance_gift=Decimal("5.00"))
    existing = SimpleNamespace(
        customer_id=7,
        order_id=90,
        payment_id=1001,
        store_id=1,
        delta_principal=Decimal("-7.00"),
        delta_gift=Decimal("-3.00"),
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result(customer), _result(existing)])

    with pytest.raises(AppException) as exc:
        await BalanceService.debit_for_order(
            db,
            customer_id=7,
            order_id=90,
            amount=Decimal("9.99"),
            store_id=1,
            payment_id=1001,
        )

    assert exc.value.code == 40994
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_wallet_refund_idempotency_rejects_changed_amount():
    from app.core.exceptions import AppException

    customer = SimpleNamespace(id=7, balance_principal=Decimal("0.00"), balance_gift=Decimal("0.00"))
    existing = SimpleNamespace(
        entry_type="REFUND_RESTORE",
        customer_id=7,
        order_id=90,
        delta_principal=Decimal("2.33"),
        delta_gift=Decimal("1.00"),
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result(customer), _result(existing)])

    with pytest.raises(AppException) as exc:
        await BalanceService.restore_for_refund(
            db,
            customer_id=7,
            order_id=90,
            refund_id=501,
            amount=Decimal("4.00"),
            store_id=1,
        )

    assert exc.value.code == 40994
    db.add.assert_not_called()
    db.flush.assert_not_awaited()

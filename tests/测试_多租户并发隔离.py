"""多租户与并发隔离：串店扣库存、余额锁定读幂等、门店账号禁止跨店合并、班次按店归集。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import AppException


@pytest.mark.asyncio
async def test_deduct_stock_rejects_other_store_product():
    from app.services.stock_service import deduct_stock

    seen: list[str] = []

    async def execute(stmt, params=None):
        sql = str(stmt).lower()
        seen.append(sql)
        res = MagicMock()
        if "from products" in sql and "for update" in sql:
            res.first.return_value = (2, "ON_SALE")
            return res
        res.rowcount = 1
        return res

    db = AsyncMock()
    db.execute = execute
    with pytest.raises(AppException) as ei:
        await deduct_stock(db, product_id=9, qty=1, store_id=1)
    assert ei.value.code == 40031
    assert not any("update products" in sql or "update store_product" in sql for sql in seen)


@pytest.mark.asyncio
async def test_balance_debit_locks_customer_before_ledger_and_stays_idempotent():
    from app.services.balance_service import BalanceService

    customer = SimpleNamespace(
        id=1,
        balance_gift=Decimal("8.00"),
        balance_principal=Decimal("2.00"),
    )
    ledger = SimpleNamespace(
        id=4, customer_id=1, order_id=8, payment_id=None, store_id=1,
        delta_principal=Decimal("-2.00"), delta_gift=Decimal("-1.00"),
    )
    order: list[str] = []

    async def execute(stmt, params=None):
        sql = str(stmt).lower()
        order.append(sql)
        res = MagicMock()
        if "from customer_write_gates" in sql:
            res.scalars.return_value.all.return_value = [1]
            return res
        if "from customers" in sql:
            res.scalar_one_or_none.return_value = customer
            return res
        if "balance_ledger" in sql:
            assert "for update" in sql
            res.scalar_one_or_none.return_value = ledger
            return res
        raise AssertionError(sql)

    db = AsyncMock()
    db.execute = execute
    db.add = MagicMock()
    out = await BalanceService.debit_for_order(
        db, customer_id=1, order_id=8, amount=Decimal("3.00"), store_id=1
    )
    assert out is ledger
    db.add.assert_not_called()
    assert "customer_write_gates" in order[0]
    assert "customers" in order[1]
    assert "balance_ledger" in order[2]


@pytest.mark.asyncio
async def test_store_staff_merge_blocked_when_source_has_other_store_orders():
    from app.services.customer_merge_service import CustomerMergeService

    source = SimpleNamespace(id=2, status=1)
    target = SimpleNamespace(id=1, status=1)
    order_locks = MagicMock()
    recharge_locks = MagicMock()
    locked_customers = MagicMock()
    locked_customers.scalars.return_value.all.return_value = [target, source]
    foreign_order = MagicMock()
    foreign_order.scalar_one_or_none.return_value = 50
    foreign_recharge = MagicMock()
    foreign_recharge.scalar_one_or_none.return_value = None
    db = AsyncMock()
    # merge() now locks payment parents (Orders, RechargeOrders) before Customers,
    # then performs the store-scope authorization probes.
    gate_locks = MagicMock()
    gate_locks.scalars.return_value.all.return_value = [1, 2]
    db.execute = AsyncMock(
        side_effect=[
            gate_locks,
            order_locks,
            recharge_locks,
            locked_customers,
            foreign_order,
            foreign_recharge,
        ]
    )

    with pytest.raises(AppException) as ei:
        await CustomerMergeService.merge(
            db,
            target_customer_id=1,
            source_customer_id=2,
            actor_store_id=1,
        )
    assert ei.value.code == 40301


@pytest.mark.asyncio
async def test_shift_totals_filter_by_store():
    from app.services.pos_shift_service import _aggregate_payments

    captured: list[str] = []

    async def execute(stmt, params=None):
        captured.append(str(stmt).lower())
        res = MagicMock()
        res.all.return_value = []
        return res

    db = AsyncMock()
    db.execute = execute
    stats = await _aggregate_payments(
        db,
        operator_id=3,
        since=datetime(2026, 9, 28, tzinfo=timezone.utc),
        until=datetime(2026, 9, 29, tzinfo=timezone.utc),
        store_id=7,
    )
    assert stats["total_amount"] == Decimal("0")
    sql = captured[0]
    assert "orders" in sql and "recharge_orders" in sql
    assert "store_id" in sql

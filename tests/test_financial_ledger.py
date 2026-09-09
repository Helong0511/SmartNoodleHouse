"""财务分录入账与净收入（允许为负）。"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.models import FinancialLedger, Store
from app.services.financial_ledger_service import FinancialLedgerService
from app.services.revenue_stats import (
    ledger_gross_since,
    ledger_refunded_since,
    net_revenue_from_ledger_since,
)

_TZ = timezone(timedelta(hours=8))


@pytest.mark.asyncio
async def test_record_sale_and_refund_writes_ledger():
    """用 mock session 验证入账写入与幂等（SQLite BigInteger 无自增，避免真实 flush）。"""
    db = MagicMock()
    empty = MagicMock()
    empty.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=empty)
    db.flush = AsyncMock()
    db.add = MagicMock()

    sale = await FinancialLedgerService.record_sale(
        db,
        store_id=1,
        amount=Decimal("100.00"),
        channel="WECHAT",
        provider="WECHAT",
        order_id=10,
        payment_id=101,
        remark="pay-101",
    )
    assert sale is not None
    assert sale.entry_type == "SALE"
    assert sale.amount == Decimal("100.00")
    db.add.assert_called()
    db.flush.assert_awaited()

    # 幂等：已有同 payment_id 的 SALE
    existing = MagicMock()
    existing.id = 9
    existing.entry_type = "SALE"
    hit = MagicMock()
    hit.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=hit)
    db.add.reset_mock()
    sale2 = await FinancialLedgerService.record_sale(
        db,
        store_id=1,
        amount=Decimal("100.00"),
        channel="WECHAT",
        provider="WECHAT",
        order_id=10,
        payment_id=101,
    )
    assert sale2.id == 9
    db.add.assert_not_called()

    empty2 = MagicMock()
    empty2.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=empty2)
    refund = await FinancialLedgerService.record_refund(
        db,
        store_id=1,
        amount=Decimal("30.00"),
        channel="WECHAT",
        provider="WECHAT",
        order_id=10,
        payment_id=101,
        refund_id=201,
        remark="rf-201",
    )
    assert refund is not None
    assert refund.entry_type == "REFUND"
    assert refund.amount == Decimal("30.00")


@pytest.mark.asyncio
async def test_ledger_net_revenue_can_be_negative(db_session):
    store = Store(id=2, store_no="S02", name="净负店", status=1)
    db_session.add(store)
    await db_session.flush()

    since = datetime(2026, 9, 1, tzinfo=_TZ)
    db_session.add(
        FinancialLedger(
            id=1,
            store_id=store.id,
            entry_type="SALE",
            channel="POS",
            provider="CASH",
            amount=Decimal("50.00"),
            order_id=1,
            payment_id=1,
            created_at=since + timedelta(hours=1),
        )
    )
    db_session.add(
        FinancialLedger(
            id=2,
            store_id=store.id,
            entry_type="REFUND",
            channel="POS",
            provider="CASH",
            amount=Decimal("80.00"),
            order_id=1,
            refund_id=2,
            created_at=since + timedelta(hours=2),
        )
    )
    await db_session.flush()

    rows = (await db_session.execute(select(FinancialLedger))).scalars().all()
    assert len(rows) == 2

    gross = await ledger_gross_since(db_session, since=since, store_id=store.id)
    refunded = await ledger_refunded_since(db_session, since=since, store_id=store.id)
    net = await net_revenue_from_ledger_since(db_session, since=since, store_id=store.id)

    assert gross == Decimal("50.00")
    assert refunded == Decimal("80.00")
    assert net == Decimal("-30.00")
    assert net < 0  # 负净收入不被强制为 0

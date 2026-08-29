"""营收统计辅助函数单元测试。"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.revenue_stats import (
    build_period_stats,
    delivery_kpi,
    member_discount_since,
    new_store_customers_since,
    order_amount_stats_since,
    resolve_period,
)

_TZ = timezone(timedelta(hours=8))


def test_resolve_period_today():
    since, until, label = resolve_period("today")
    assert label == "今日"
    assert until >= since


def test_resolve_period_month():
    since, until, label = resolve_period("month")
    assert label == "本月"
    assert since.day == 1


def test_resolve_period_yesterday():
    since, until, label = resolve_period("yesterday")
    assert label == "昨日"
    assert until > since


@pytest.mark.asyncio
async def test_member_discount_since_returns_money():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=Decimal("12.50"))
    result = await member_discount_since(db, since=MagicMock())
    assert result == Decimal("12.50")


@pytest.mark.asyncio
async def test_member_discount_since_empty():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)
    result = await member_discount_since(db, since=MagicMock(), store_id=1)
    assert result == Decimal("0.00")


@pytest.mark.asyncio
async def test_order_amount_stats_since():
    db = MagicMock()
    row = MagicMock()
    row.one.return_value = (
        Decimal("1000.00"),
        Decimal("20.00"),
        Decimal("15.00"),
        Decimal("10.00"),
        Decimal("955.00"),
        Decimal("5.00"),
    )
    db.execute = AsyncMock(return_value=row)
    result = await order_amount_stats_since(db, since=MagicMock())
    assert result["member_discount"] == Decimal("20.00")
    assert result["coupon_discount"] == Decimal("15.00")
    assert result["voucher_discount"] == Decimal("10.00")
    assert result["total_discount"] == Decimal("45.00")


@pytest.mark.asyncio
async def test_new_store_customers_since_with_until():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=1)
    since = datetime(2026, 8, 28, 0, 0, tzinfo=_TZ)
    until = datetime(2026, 8, 29, 0, 0, tzinfo=_TZ)
    result = await new_store_customers_since(db, since=since, until=until, store_id=1)
    assert result == 1
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_period_stats_passes_until_to_new_store_customers():
    db = MagicMock()
    since = datetime(2026, 8, 28, 0, 0, tzinfo=_TZ)
    until = datetime(2026, 8, 29, 0, 0, tzinfo=_TZ)
    with patch(
        "app.services.revenue_stats.placed_order_count_since", new_callable=AsyncMock, return_value=0
    ), patch(
        "app.services.revenue_stats.paid_order_count_since", new_callable=AsyncMock, return_value=0
    ), patch(
        "app.services.revenue_stats.gross_revenue_since",
        new_callable=AsyncMock,
        return_value=Decimal("0"),
    ), patch(
        "app.services.revenue_stats.refunded_since", new_callable=AsyncMock, return_value=Decimal("0")
    ), patch(
        "app.services.revenue_stats.order_amount_stats_since",
        new_callable=AsyncMock,
        return_value={
            "subtotal": Decimal("0"),
            "member_discount": Decimal("0"),
            "coupon_discount": Decimal("0"),
            "voucher_discount": Decimal("0"),
            "total_discount": Decimal("0"),
            "payable_amount": Decimal("0"),
            "packing_fee": Decimal("0"),
        },
    ), patch(
        "app.services.revenue_stats.new_registered_members_since", new_callable=AsyncMock, return_value=0
    ), patch(
        "app.services.revenue_stats.new_store_customers_since", new_callable=AsyncMock, return_value=3
    ) as mock_new:
        stats = await build_period_stats(
            db, since=since, until=until, period_label="昨日", store_id=1
        )
        mock_new.assert_awaited_once_with(db, since=since, until=until, store_id=1)
        assert stats["new_store_customers"] == 3


@pytest.mark.asyncio
async def test_delivery_kpi_status_counts_use_period_when_until_set():
    db = MagicMock()
    since = datetime(2026, 8, 28, 0, 0, tzinfo=_TZ)
    until = datetime(2026, 8, 29, 0, 0, tzinfo=_TZ)
    scalar_results = iter([0, 0, 0, 0, 0, 0, 0])
    db.scalar = AsyncMock(side_effect=lambda *_: next(scalar_results, 0))
    with patch(
        "app.services.revenue_stats.gross_revenue_since",
        new_callable=AsyncMock,
        return_value=Decimal("0"),
    ), patch(
        "app.services.revenue_stats.refunded_since", new_callable=AsyncMock, return_value=Decimal("0")
    ), patch(
        "app.services.revenue_stats.member_discount_since",
        new_callable=AsyncMock,
        return_value=Decimal("0"),
    ):
        result = await delivery_kpi(db, since=since, until=until, store_id=1)
        assert result["pending"] == 0
        stmts = [call.args[0] for call in db.scalar.call_args_list if call.args]
        period_sql = " ".join(str(s) for s in stmts)
        assert "created_at" in period_sql


@pytest.mark.asyncio
async def test_delivery_kpi_status_counts_live_snapshot_without_until():
    db = MagicMock()
    since = datetime(2026, 8, 29, 0, 0, tzinfo=_TZ)
    scalar_results = iter([0, 5, 1, 2, 0, 0, 0, 0, 0])
    db.scalar = AsyncMock(side_effect=lambda *_: next(scalar_results, 0))
    with patch(
        "app.services.revenue_stats.gross_revenue_since",
        new_callable=AsyncMock,
        return_value=Decimal("0"),
    ), patch(
        "app.services.revenue_stats.refunded_since", new_callable=AsyncMock, return_value=Decimal("0")
    ), patch(
        "app.services.revenue_stats.member_discount_since",
        new_callable=AsyncMock,
        return_value=Decimal("0"),
    ):
        result = await delivery_kpi(db, since=since, until=None, store_id=1)
        assert result["pending"] == 5
        stmts = [call.args[0] for call in db.scalar.call_args_list if call.args]
        status_stmt = str(stmts[1])
        assert "created_at" not in status_stmt

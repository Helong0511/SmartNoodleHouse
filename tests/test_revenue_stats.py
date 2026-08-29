"""营收统计辅助函数单元测试。"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.revenue_stats import member_discount_since, order_amount_stats_since, resolve_period


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

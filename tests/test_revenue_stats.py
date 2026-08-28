"""营收统计辅助函数单元测试。"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.revenue_stats import member_discount_since


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

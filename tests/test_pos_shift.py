"""收银班次与日结逻辑测试。"""
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.pos_shift_service import PosShiftService, _day_bounds, _money


def test_money_none():
    assert _money(None) == Decimal("0.00")


def test_day_bounds_default():
    start, end = _day_bounds(None)
    assert start < end
    assert (end - start).days == 1


@pytest.mark.asyncio
async def test_open_shift_rejects_duplicate():
    db = AsyncMock()
    existing = MagicMock()
    PosShiftService.get_open_shift = AsyncMock(return_value=existing)
    with pytest.raises(Exception):
        await PosShiftService.open_shift(
            db, store_id=1, operator_id=2, opening_float=Decimal("100")
        )


@pytest.mark.asyncio
async def test_list_shift_orders_empty():
    from datetime import timezone

    db = AsyncMock()
    shift = MagicMock()
    shift.id = 1
    shift.operator_id = 2
    shift.opened_at = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    shift.closed_at = None

    count_result = MagicMock()
    count_result.scalar.return_value = 0
    list_result = MagicMock()
    list_result.scalars.return_value.all.return_value = []

    db.execute = AsyncMock(side_effect=[count_result, list_result])
    data = await PosShiftService.list_shift_orders(db, shift)
    assert data["total"] == 0
    assert data["items"] == []

"""会员积分与等级升级逻辑测试。"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.customer_service import CustomerService


def _level(lid: int, name: str, min_pts: int, rate: str):
    return SimpleNamespace(id=lid, name=name, min_points=min_pts, discount_rate=Decimal(rate))


def test_member_progress_payload_next_level():
    customer = SimpleNamespace(points=50, member_level_id=1)
    level = _level(1, "普通会员", 0, "1.0000")
    levels = [
        _level(1, "普通会员", 0, "1.0000"),
        _level(2, "银卡会员", 100, "0.9800"),
        _level(3, "金卡会员", 500, "0.9500"),
    ]
    payload = CustomerService.member_progress_payload(customer, level, levels)
    assert payload["next_level_name"] == "银卡会员"
    assert payload["points_to_next_level"] == 50
    assert payload["level_progress_percent"] == 50


def test_discount_display_text():
    assert CustomerService.discount_display_text(Decimal("0.98")) == "9.8折"
    assert CustomerService.discount_display_text(Decimal("1")) is None


@pytest.mark.asyncio
async def test_recalculate_member_level_upgrades():
    customer = SimpleNamespace(points=120, member_level_id=1)
    levels = [
        _level(1, "普通会员", 0, "1.0000"),
        _level(2, "银卡会员", 100, "0.9800"),
    ]
    db = MagicMock()
    db.flush = AsyncMock()
    CustomerService.list_levels_ordered = AsyncMock(return_value=levels)  # type: ignore[method-assign]
    result = await CustomerService.recalculate_member_level(db, customer)
    assert result.name == "银卡会员"
    assert customer.member_level_id == 2

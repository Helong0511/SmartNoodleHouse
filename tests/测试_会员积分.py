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


@pytest.mark.asyncio
async def test_deduct_points_for_refund_downgrades():
    customer = SimpleNamespace(id=1, points=150, member_level_id=2)
    order = SimpleNamespace(customer_id=1, id=9)
    levels = [
        _level(1, "普通会员", 0, "1.0000"),
        _level(2, "银卡会员", 100, "0.9800"),
    ]
    db = MagicMock()
    locked = MagicMock()
    locked.scalar_one_or_none.return_value = customer
    db.execute = AsyncMock(return_value=locked)
    # EARN 合计 / 已 REFUND_DEDUCT 合计（本单净可扣上限）
    db.scalar = AsyncMock(side_effect=[60, 0])
    db.add = MagicMock()
    db.flush = AsyncMock()
    CustomerService.list_levels_ordered = AsyncMock(return_value=levels)  # type: ignore[method-assign]
    deducted = await CustomerService.deduct_points_for_refund(db, order, Decimal("60.00"))
    assert deducted == 60
    assert customer.points == 90
    assert customer.member_level_id == 1


@pytest.mark.asyncio
async def test_deduct_points_for_refund_clamps_at_zero():
    customer = SimpleNamespace(id=1, points=30, member_level_id=2)
    order = SimpleNamespace(customer_id=1, id=9)
    levels = [
        _level(1, "普通会员", 0, "1.0000"),
        _level(2, "银卡会员", 100, "0.9800"),
    ]
    db = MagicMock()
    locked = MagicMock()
    locked.scalar_one_or_none.return_value = customer
    db.execute = AsyncMock(return_value=locked)
    db.scalar = AsyncMock(side_effect=[50, 0])
    db.add = MagicMock()
    db.flush = AsyncMock()
    CustomerService.list_levels_ordered = AsyncMock(return_value=levels)  # type: ignore[method-assign]
    deducted = await CustomerService.deduct_points_for_refund(db, order, Decimal("50.00"))
    assert deducted == 30  # 仅扣现有积分，不虚报
    assert customer.points == 0
    assert customer.member_level_id == 1


@pytest.mark.asyncio
async def test_deduct_points_capped_by_order_net_earn():
    """退款扣分不超过本单净已发积分，避免跨单扣光。"""
    customer = SimpleNamespace(id=1, points=200, member_level_id=2)
    order = SimpleNamespace(customer_id=1, id=9)
    levels = [
        _level(1, "普通会员", 0, "1.0000"),
        _level(2, "银卡会员", 100, "0.9800"),
    ]
    db = MagicMock()
    locked = MagicMock()
    locked.scalar_one_or_none.return_value = customer
    db.execute = AsyncMock(return_value=locked)
    db.scalar = AsyncMock(side_effect=[40, 0])  # 本单仅发过 40
    db.add = MagicMock()
    db.flush = AsyncMock()
    CustomerService.list_levels_ordered = AsyncMock(return_value=levels)  # type: ignore[method-assign]
    deducted = await CustomerService.deduct_points_for_refund(db, order, Decimal("80.00"))
    assert deducted == 40
    assert customer.points == 160


def test_points_from_amount_rounds_half_up():
    assert CustomerService.points_from_amount(Decimal("10.4")) == 10
    assert CustomerService.points_from_amount(Decimal("10.5")) == 11
    assert CustomerService.points_from_amount(Decimal("10.49")) == 10

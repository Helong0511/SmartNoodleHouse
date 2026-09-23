"""会员等级后台：银卡/金卡升级积分门槛校验与重算。"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.customer_service import CustomerService


def _level(lid: int, name: str, min_pts: int, rate: str):
    return SimpleNamespace(id=lid, name=name, min_points=min_pts, discount_rate=Decimal(rate))


def _base_levels():
    return [
        _level(1, "普通会员", 0, "1.0000"),
        _level(2, "银卡会员", 100, "0.9800"),
        _level(3, "金卡会员", 500, "0.9500"),
    ]


def test_resolve_accepts_silver_gold_threshold_change():
    planned = CustomerService.resolve_level_updates(
        _base_levels(),
        [
            {"id": 1, "min_points": 0, "discount_rate": "1.0000"},
            {"id": 2, "min_points": 200, "discount_rate": "0.9700"},
            {"id": 3, "min_points": 800, "discount_rate": "0.9300"},
        ],
    )
    by_name = {lv.name: (pts, rate) for lv, pts, rate in planned}
    assert by_name["银卡会员"][0] == 200
    assert by_name["金卡会员"][0] == 800
    assert by_name["银卡会员"][1] == Decimal("0.97")


def test_resolve_rejects_gold_not_higher_than_silver():
    with pytest.raises(AppException) as ei:
        CustomerService.resolve_level_updates(
            _base_levels(),
            [
                {"id": 1, "min_points": 0, "discount_rate": "1"},
                {"id": 2, "min_points": 300, "discount_rate": "0.98"},
                {"id": 3, "min_points": 300, "discount_rate": "0.95"},
            ],
        )
    assert ei.value.code == 40095


def test_resolve_rejects_base_level_nonzero_points():
    with pytest.raises(AppException) as ei:
        CustomerService.resolve_level_updates(
            _base_levels(),
            [
                {"id": 1, "min_points": 10, "discount_rate": "1"},
                {"id": 2, "min_points": 100, "discount_rate": "0.98"},
                {"id": 3, "min_points": 500, "discount_rate": "0.95"},
            ],
        )
    assert ei.value.code == 40094


def test_resolve_rejects_invalid_discount():
    with pytest.raises(AppException) as ei:
        CustomerService.resolve_level_updates(
            _base_levels(),
            [
                {"id": 2, "min_points": 100, "discount_rate": "1.2"},
            ],
        )
    assert ei.value.code == 40093


def test_resolve_rejects_unknown_id():
    with pytest.raises(AppException) as ei:
        CustomerService.resolve_level_updates(_base_levels(), [{"id": 99, "min_points": 10, "discount_rate": "1"}])
    assert ei.value.code == 40091


def test_level_admin_payload_locks_base_min_points():
    payload = CustomerService.level_admin_payload(_level(1, "普通会员", 0, "1.0000"), 12)
    assert payload["is_base"] is True
    assert payload["editable_min_points"] is False
    assert payload["member_count"] == 12
    gold = CustomerService.level_admin_payload(_level(3, "金卡会员", 500, "0.9500"))
    assert gold["editable_min_points"] is True
    assert gold["discount_text"] == "9.5折"


@pytest.mark.asyncio
async def test_recalculate_all_assigns_highest_reachable():
    levels = _base_levels()
    db = MagicMock()
    db.scalar = AsyncMock(return_value=4)
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    with patch.object(CustomerService, "list_levels_ordered", AsyncMock(return_value=levels)):
        total = await CustomerService.recalculate_all_member_levels(db)
    assert total == 4
    assert db.execute.await_count == 3
    db.flush.assert_awaited()

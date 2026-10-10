"""口味场景加权 + 规格组门店隔离。"""
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.preference_scene import (
    apply_scene_multiplier,
    calendar_season,
    code_boost,
)
from app.api.products import _assert_option_group_manage, _assert_groups_usable_for_store
from app.core.exceptions import AppException


def test_calendar_season_months():
    assert calendar_season(datetime(2026, 1, 15)) == "winter"
    assert calendar_season(datetime(2026, 4, 1)) == "spring"
    assert calendar_season(datetime(2026, 7, 1)) == "summer"
    assert calendar_season(datetime(2026, 10, 1)) == "autumn"


def test_summer_boosts_mild_over_base():
    base = 0.5
    boosted = apply_scene_multiplier(
        base,
        option_code="MILD",
        season="summer",
        weather_condition="hot",
        last_used_at=None,
        now=datetime(2026, 7, 1, 12, 0, 0),
    )
    assert boosted > base


def test_winter_boosts_hot():
    base = 0.4
    boosted = apply_scene_multiplier(
        base,
        option_code="HOT",
        season="winter",
        weather_condition="cold",
        now=datetime(2026, 1, 10, 12, 0, 0),
    )
    assert boosted > base


def test_same_weekday_meal_boost():
    base = 0.5
    last = datetime(2026, 9, 8, 12, 0, 0)  # 周二午
    now = datetime(2026, 9, 15, 12, 30, 0)  # 下周二午
    with_boost = apply_scene_multiplier(
        base, option_code="ZZZ_UNKNOWN", season="autumn", last_used_at=last, now=now
    )
    assert with_boost > base


def test_code_boost_fuzzy():
    from decimal import Decimal as D

    m = {"HOT": D("1.1")}
    assert code_boost("EXTRA_HOT", m) == D("1.1")


def test_store_staff_cannot_edit_hq_group():
    user = SimpleNamespace(store_id=1, role=SimpleNamespace(code="STORE_MANAGER"))
    group = SimpleNamespace(store_id=None)
    with pytest.raises(AppException) as ei:
        _assert_option_group_manage(user, group)
    assert ei.value.code == 40301


def test_store_staff_can_edit_own_group():
    user = SimpleNamespace(store_id=2, role=SimpleNamespace(code="STORE_MANAGER"))
    group = SimpleNamespace(store_id=2)
    _assert_option_group_manage(user, group)


@pytest.mark.asyncio
async def test_groups_usable_rejects_other_store():
    db = MagicMock()
    g_hq = SimpleNamespace(id=1, store_id=None, status=1)
    g_other = SimpleNamespace(id=2, store_id=9, status=1)
    result = MagicMock()
    result.scalars.return_value.all.return_value = [g_hq, g_other]
    db.execute = AsyncMock(return_value=result)
    with pytest.raises(AppException) as ei:
        await _assert_groups_usable_for_store(db, [1, 2], store_id=3)
    assert "不属于本店" in str(ei.value.message)

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
    resolve_affinities,
)
from app.services.recommendation_service import (
    PoolProduct,
    _scene_multiplier,
    _weather_reason_for,
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


def test_store_prefixed_codes_get_weather_boost():
    """门店前缀拼音码（真实种子）必须能命中天气亲和。"""
    base = 0.5
    hot_mild = apply_scene_multiplier(
        base,
        option_code="SH01-La-WeiLa",
        option_name="微辣",
        season="summer",
        weather_condition="hot",
        now=datetime(2026, 7, 1, 12, 0, 0),
    )
    cold_jia = apply_scene_multiplier(
        base,
        option_code="SH01-La-JiaLa",
        option_name="加辣",
        season="winter",
        weather_condition="cold",
        now=datetime(2026, 1, 10, 12, 0, 0),
    )
    assert hot_mild > base
    assert cold_jia > base
    assert "mild_spicy" in resolve_affinities("SH01-La-WeiLa", "微辣")
    assert "hot_spicy" in resolve_affinities("SH01-La-JiaLa", "加辣")
    assert "no_spicy" in resolve_affinities("SH01-La-BuLa", "不辣")


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
    assert code_boost("SH01-La-JiaLa", {"hot_spicy": D("1.1")}, option_name="加辣") == D("1.1")


def test_product_reco_weather_condition_keys():
    """产品推荐天气乘子须使用 weather_client 的 lowercase condition。"""
    p = PoolProduct(
        id=1,
        name="冰粉",
        price=Decimal("5"),
        main_image=None,
        category_id=1,
        category_name="饮品",
        is_hot=False,
        is_recommended=False,
        sales_count=0,
        created_at=None,
        stock=10,
        tags=[],
        dimensions={"temperature": 0},
    )
    mult_hot = _scene_multiplier(p, {"season": "summer", "condition": "hot"})
    mult_legacy_upper = _scene_multiplier(p, {"season": "summer", "condition": "HOT"})
    mult_rain_old = _scene_multiplier(
        PoolProduct(
            id=2,
            name="蹄花面",
            price=Decimal("14"),
            main_image=None,
            category_id=1,
            category_name="招牌面",
            is_hot=False,
            is_recommended=False,
            sales_count=0,
            created_at=None,
            stock=10,
            tags=["浓汤"],
            dimensions={"temperature": 3},
        ),
        {"season": "winter", "condition": "rainy"},
    )
    assert mult_hot > 1.0
    # 输入大小写归一，兼容旧调用
    assert mult_legacy_upper == mult_hot
    assert mult_rain_old > 1.0
    assert _weather_reason_for(p, {"condition": "hot"}) == "天气炎热更清爽"


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

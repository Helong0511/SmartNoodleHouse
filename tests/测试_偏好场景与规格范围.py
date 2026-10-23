"""口味场景加权 + 规格组门店隔离 + 天气口味加权。"""
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.preference_scene import (
    apply_scene_multiplier,
    calendar_season,
    code_boost,
    resolve_affinities,
    scene_ops_payload,
    scene_summary_label,
    weather_affinity_hint,
)
from app.services.recommendation_service import (
    PoolProduct,
    _product_scene_flags,
    _scene_multiplier,
    _weather_reason_for,
)
from app.services.weather_client import (
    COLD_TEMP_C,
    HOT_TEMP_C,
    condition_from_wmo,
    weather_thresholds,
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


def test_condition_from_wmo_thresholds():
    """雨天优先于气温；热/冷阈值与 weather_client 常量一致。"""
    assert condition_from_wmo(61, 20) == "rainy"
    assert condition_from_wmo(0, HOT_TEMP_C) == "hot"
    assert condition_from_wmo(0, HOT_TEMP_C - 0.1) == "mild"
    assert condition_from_wmo(0, COLD_TEMP_C) == "cold"
    assert condition_from_wmo(0, COLD_TEMP_C + 0.1) == "mild"
    th = weather_thresholds()
    assert th["hot_temp_c"] == HOT_TEMP_C
    assert th["cold_temp_c"] == COLD_TEMP_C


def test_weather_affinity_hint_and_label():
    assert "不辣" in (weather_affinity_hint("hot") or "") or "清汤" in (weather_affinity_hint("hot") or "")
    assert "加辣" in (weather_affinity_hint("cold") or "")
    label = scene_summary_label(
        {
            "enabled": True,
            "season": "summer",
            "condition": "hot",
            "temp_c": 32,
            "weather_active": True,
        }
    )
    assert label and "夏季" in label and "炎热" in label and "32" in label
    assert scene_summary_label({"enabled": False, "season": "summer"}) is None
    ops = scene_ops_payload({"scene_enabled": True, "condition": "hot", "weather_active": True})
    assert "thresholds" in ops and "howto" in ops
    assert "hot" in ops["affinity_hints"]


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
    # 总开关关闭：乘子与天气理由均失效
    assert _scene_multiplier(p, {"scene_enabled": False, "season": "summer", "condition": "hot"}) == 1.0
    assert _weather_reason_for(p, {"scene_enabled": False, "condition": "hot"}) is None


def test_product_spicy_mild_weather_flags():
    """辣度维参与天气乘子：炎热抬升清淡、寒冷抬升麻辣。"""
    mild_p = PoolProduct(
        id=3,
        name="清汤面",
        price=Decimal("12"),
        main_image=None,
        category_id=1,
        category_name="招牌面",
        is_hot=False,
        is_recommended=False,
        sales_count=0,
        created_at=None,
        stock=10,
        tags=["清淡"],
        dimensions={"spicy": 0, "temperature": 2},
    )
    spicy_p = PoolProduct(
        id=4,
        name="麻辣牛肉面",
        price=Decimal("16"),
        main_image=None,
        category_id=1,
        category_name="招牌面",
        is_hot=False,
        is_recommended=False,
        sales_count=0,
        created_at=None,
        stock=10,
        tags=["麻辣"],
        dimensions={"spicy": 3, "temperature": 3},
    )
    flags_m = _product_scene_flags(mild_p)
    flags_s = _product_scene_flags(spicy_p)
    assert flags_m["mild"] and not flags_m["spicy"]
    assert flags_s["spicy"] and not flags_s["mild"]
    assert _scene_multiplier(mild_p, {"season": "summer", "condition": "hot"}) > 1.0
    assert _scene_multiplier(spicy_p, {"season": "winter", "condition": "cold"}) > 1.0


@pytest.mark.asyncio
async def test_build_scene_context_weather_off():
    """天气子开关关闭时：季节仍在，condition 为空，带 reason。"""
    from app.services.preference_scene import build_scene_context

    db = MagicMock()

    async def _eff(db_, key, default=True, store_id=None):
        if key == "preference_scene_enabled":
            return True
        if key == "weather_pref_enabled":
            return False
        return default

    with patch(
        "app.services.system_settings_service.SystemSettingsService.effective_bool",
        new=AsyncMock(side_effect=_eff),
    ):
        ctx = await build_scene_context(db, store_id=1)
    assert ctx["scene_enabled"] is True
    assert ctx["weather_pref_enabled"] is False
    assert ctx["weather_active"] is False
    assert ctx["condition"] is None
    assert "天气口味加权已关闭" in (ctx["weather_reason"] or "")
    assert ctx["season"] in ("spring", "summer", "autumn", "winter")


@pytest.mark.asyncio
async def test_build_scene_context_scene_off():
    from app.services.preference_scene import build_scene_context

    db = MagicMock()

    async def _eff(db_, key, default=True, store_id=None):
        return False if key == "preference_scene_enabled" else True

    with patch(
        "app.services.system_settings_service.SystemSettingsService.effective_bool",
        new=AsyncMock(side_effect=_eff),
    ):
        ctx = await build_scene_context(db, store_id=1)
    assert ctx["scene_enabled"] is False
    assert ctx["label"] is None
    assert "口味场景加权已关闭" in (ctx["weather_reason"] or "")


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

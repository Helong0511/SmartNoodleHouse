"""口味场景加权：季节 / 天气 / 同星期同餐段（读侧微调，不改历史重算）。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.response import now_local

# 按 option_code 的季节亲和（面馆常见编码；未命中不加权）
_SEASON_CODE_BOOST: dict[str, dict[str, Decimal]] = {
    "spring": {
        "MILD": Decimal("1.06"),
        "MEDIUM": Decimal("1.05"),
        "NORMAL": Decimal("1.04"),
    },
    "summer": {
        "NONE": Decimal("1.12"),
        "MILD": Decimal("1.08"),
        "LESS": Decimal("1.10"),
        "SMALL": Decimal("1.06"),
        "CLEAR": Decimal("1.10"),
        "COLD": Decimal("1.12"),
    },
    "autumn": {
        "MEDIUM": Decimal("1.06"),
        "HOT": Decimal("1.08"),
        "NORMAL": Decimal("1.04"),
    },
    "winter": {
        "HOT": Decimal("1.12"),
        "EXTRA": Decimal("1.15"),
        "EXTRA_HOT": Decimal("1.18"),
        "MORE": Decimal("1.10"),
        "LARGE": Decimal("1.08"),
        "BONE": Decimal("1.10"),
        "THICK": Decimal("1.08"),
    },
}

_WEATHER_CODE_BOOST: dict[str, dict[str, Decimal]] = {
    "hot": {
        "NONE": Decimal("1.10"),
        "MILD": Decimal("1.08"),
        "LESS": Decimal("1.08"),
        "SMALL": Decimal("1.05"),
        "CLEAR": Decimal("1.08"),
    },
    "cold": {
        "HOT": Decimal("1.10"),
        "EXTRA": Decimal("1.12"),
        "EXTRA_HOT": Decimal("1.14"),
        "MORE": Decimal("1.08"),
        "LARGE": Decimal("1.06"),
        "BONE": Decimal("1.10"),
    },
    "rainy": {
        "HOT": Decimal("1.06"),
        "BONE": Decimal("1.08"),
        "THICK": Decimal("1.06"),
        "MORE": Decimal("1.05"),
    },
    "mild": {},
}

_SAME_WEEKDAY_BOOST = Decimal("1.05")
_SAME_MEAL_BOOST = Decimal("1.05")
_MAX_SCENE_MULT = Decimal("1.35")


def calendar_season(when: datetime | None = None) -> str:
    dt = when or now_local()
    m = int(dt.month)
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    if m in (9, 10, 11):
        return "autumn"
    return "winter"


def _in_meal_hours(hour: int) -> bool:
    return hour in range(11, 15) or hour in range(17, 22)


def code_boost(option_code: str, mapping: dict[str, Decimal]) -> Decimal:
    code = (option_code or "").strip().upper()
    if not code:
        return Decimal("1")
    if code in mapping:
        return mapping[code]
    # 模糊：EXTRA_SPICY 等含 HOT
    for key, mult in mapping.items():
        if key in code or code in key:
            return mult
    return Decimal("1")


def apply_scene_multiplier(
    base_score: float | Decimal,
    *,
    option_code: str,
    last_used_at: datetime | None = None,
    season: str,
    weather_condition: str | None = None,
    now: datetime | None = None,
) -> float:
    """读侧乘子，夹在合理范围后量化。"""
    score = Decimal(str(base_score or 0))
    mult = Decimal("1")
    mult *= code_boost(option_code, _SEASON_CODE_BOOST.get(season, {}))
    if weather_condition:
        mult *= code_boost(option_code, _WEATHER_CODE_BOOST.get(weather_condition, {}))
    now_dt = now or now_local()
    if last_used_at is not None:
        try:
            if int(last_used_at.weekday()) == int(now_dt.weekday()):
                mult *= _SAME_WEEKDAY_BOOST
            if _in_meal_hours(int(last_used_at.hour)) and _in_meal_hours(int(now_dt.hour)):
                mult *= _SAME_MEAL_BOOST
        except Exception:
            pass
    if mult > _MAX_SCENE_MULT:
        mult = _MAX_SCENE_MULT
    out = score * mult
    if out > 1:
        out = Decimal("1")
    if out < 0:
        out = Decimal("0")
    return float(out.quantize(Decimal("0.0001")))


async def build_scene_context(db, store_id: int | None = None) -> dict[str, Any]:
    """组装当前场景；天气依赖门店经纬度。"""
    now = now_local()
    season = calendar_season(now)
    ctx: dict[str, Any] = {
        "season": season,
        "weekday": int(now.weekday()),
        "hour": int(now.hour),
        "weather": None,
        "temp_c": None,
        "condition": None,
        "store_id": int(store_id) if store_id else None,
    }
    if not store_id:
        return ctx

    enabled = True
    try:
        from app.services.system_settings_service import SystemSettingsService

        enabled = await SystemSettingsService.effective_bool(
            db, "weather_pref_enabled", True, store_id=int(store_id)
        )
    except Exception:
        enabled = True
    if not enabled:
        return ctx

    try:
        from app.models import Store

        store = await db.get(Store, int(store_id))
        lat = getattr(store, "latitude", None) if store else None
        lng = getattr(store, "longitude", None) if store else None
        if lat is None or lng is None:
            return ctx
        from app.services.weather_client import fetch_current_weather

        w = await fetch_current_weather(float(lat), float(lng))
        if not w:
            return ctx
        ctx["weather"] = w
        ctx["temp_c"] = w.get("temp_c")
        ctx["condition"] = w.get("condition")
    except Exception:
        pass
    return ctx

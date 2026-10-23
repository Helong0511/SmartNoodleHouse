"""口味场景加权：季节 / 天气 / 同星期同餐段（读侧微调，不改历史重算）。

规格编码已改为门店前缀拼音（如 SH01-La-BuLa），不能再只认 NONE/MILD/HOT。
亲和匹配同时看 option_code 与 option_name，与 taste_profile_service 词表对齐。
天气 condition 与 weather_client 一致：hot / cold / rainy / mild。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.response import now_local
from app.services.weather_client import CONDITION_LABELS as _CONDITION_LABELS

# 亲和键 → 同义词（英文码片段 / 拼音码尾 / 中文名）；最长匹配优先
_AFFINITY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "no_spicy": (
        "NONE", "NO_SPICY", "BULA", "不辣", "无辣", "免辣", "清汤", "QINGTANG",
    ),
    "mild_spicy": (
        "MILD", "LIGHT_SPICY", "WEILA", "微辣", "小辣", "一点点辣",
    ),
    "medium_spicy": (
        "MEDIUM", "NORMAL", "ZHENGCHANGLA", "正常辣", "中辣",
    ),
    "hot_spicy": (
        "HOT", "SPICY", "JIALA", "加辣", "重辣", "香辣",
    ),
    "extra_hot": (
        "EXTRA_HOT", "EXTRASPICY", "EXTRA", "特辣", "变态辣", "爆辣", "魔鬼辣",
    ),
    "less_amt": (
        "LESS", "SMALL", "XIAOWAN", "SHAOTANG", "小碗", "少汤", "少糖",
    ),
    "more_amt": (
        "MORE", "LARGE", "DAWAN", "DUOTANG", "JIALIANG", "大碗", "多汤", "加量",
    ),
    "clear_soup": (
        "CLEAR", "QINGTANG", "清汤",
    ),
    "cold_item": (
        "COLD", "ICE", "ICED", "冰", "冷饮", "冰镇",
    ),
    "bone_soup": (
        "BONE", "浓汤", "蹄花", "大骨", "TIHUA", "NONG",
    ),
    "thick": (
        "THICK", "浓", "NONG",
    ),
    "normal": (
        "NORMAL", "REGULAR", "STANDARD", "BIAOZHUN", "ZHENGCHANG", "标准", "正常",
    ),
}

# 季节亲和（键为亲和键，未命中不加权）
_SEASON_AFFINITY_BOOST: dict[str, dict[str, Decimal]] = {
    "spring": {
        "mild_spicy": Decimal("1.06"),
        "medium_spicy": Decimal("1.05"),
        "normal": Decimal("1.04"),
    },
    "summer": {
        "no_spicy": Decimal("1.12"),
        "mild_spicy": Decimal("1.08"),
        "less_amt": Decimal("1.08"),
        "clear_soup": Decimal("1.10"),
        "cold_item": Decimal("1.12"),
    },
    "autumn": {
        "medium_spicy": Decimal("1.06"),
        "hot_spicy": Decimal("1.08"),
        "normal": Decimal("1.04"),
    },
    "winter": {
        "hot_spicy": Decimal("1.12"),
        "extra_hot": Decimal("1.15"),
        "more_amt": Decimal("1.10"),
        "bone_soup": Decimal("1.10"),
        "thick": Decimal("1.08"),
    },
}

_WEATHER_AFFINITY_BOOST: dict[str, dict[str, Decimal]] = {
    "hot": {
        "no_spicy": Decimal("1.10"),
        "mild_spicy": Decimal("1.08"),
        "less_amt": Decimal("1.06"),
        "clear_soup": Decimal("1.08"),
        "cold_item": Decimal("1.10"),
    },
    "cold": {
        "hot_spicy": Decimal("1.10"),
        "extra_hot": Decimal("1.14"),
        "more_amt": Decimal("1.08"),
        "bone_soup": Decimal("1.10"),
        "thick": Decimal("1.06"),
    },
    "rainy": {
        "hot_spicy": Decimal("1.06"),
        "bone_soup": Decimal("1.08"),
        "thick": Decimal("1.06"),
        "more_amt": Decimal("1.05"),
    },
    "mild": {},
}

# 兼容旧测试 / 外部调用：英文码 → 亲和键
_LEGACY_CODE_TO_AFFINITY: dict[str, str] = {
    "NONE": "no_spicy",
    "NO_SPICY": "no_spicy",
    "MILD": "mild_spicy",
    "LIGHT_SPICY": "mild_spicy",
    "MEDIUM": "medium_spicy",
    "NORMAL": "normal",
    "HOT": "hot_spicy",
    "SPICY": "hot_spicy",
    "EXTRA": "extra_hot",
    "EXTRA_HOT": "extra_hot",
    "EXTRASPICY": "extra_hot",
    "LESS": "less_amt",
    "SMALL": "less_amt",
    "MORE": "more_amt",
    "LARGE": "more_amt",
    "CLEAR": "clear_soup",
    "COLD": "cold_item",
    "BONE": "bone_soup",
    "THICK": "thick",
}

_SAME_WEEKDAY_BOOST = Decimal("1.05")
_SAME_MEAL_BOOST = Decimal("1.05")
_MAX_SCENE_MULT = Decimal("1.35")

_SEASON_LABELS = {
    "spring": "春季",
    "summer": "夏季",
    "autumn": "秋季",
    "winter": "冬季",
}

# 运维/探测文案：各天气 condition 对规格亲和的抬升方向（与 _WEATHER_AFFINITY_BOOST 同步）
_WEATHER_AFFINITY_HINT: dict[str, str] = {
    "hot": "炎热：抬升不辣/微辣/小碗/清汤/冰品",
    "cold": "寒冷：抬升加辣/特辣/大碗/浓汤/浓口",
    "rainy": "雨天：抬升加辣/浓汤/浓口/大碗",
    "mild": "舒适：天气不对规格额外加权（仍可有季节/同星期同餐段）",
}


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


def _norm_blob(option_code: str, option_name: str | None = None) -> tuple[str, str]:
    """返回 (compact_upper_ascii, raw_text) 供同义词匹配。"""
    code = (option_code or "").strip()
    name = (option_name or "").strip()
    raw = f"{code} {name}".strip()
    compact = raw.upper().replace(" ", "").replace("-", "").replace("_", "")
    return compact, raw


def resolve_affinities(option_code: str, option_name: str | None = None) -> list[str]:
    """解析规格命中的亲和键（最长同义词优先，可多键）。"""
    compact, raw = _norm_blob(option_code, option_name)
    if not compact and not raw:
        return []
    hits: list[tuple[int, str]] = []  # (词长, affinity)
    for aff, words in _AFFINITY_SYNONYMS.items():
        for w in words:
            if not w:
                continue
            if w.isascii():
                wu = w.upper().replace(" ", "").replace("-", "").replace("_", "")
                hit = (len(wu) >= 3 and wu in compact) or (wu == compact)
            else:
                hit = w in raw
            if hit:
                hits.append((len(w), aff))
    if not hits:
        # 纯英文码遗留：整码等于映射表
        code_u = (option_code or "").strip().upper().replace("-", "_")
        if code_u in _LEGACY_CODE_TO_AFFINITY:
            return [_LEGACY_CODE_TO_AFFINITY[code_u]]
        # EXTRA_HOT 含 HOT 等：按最长遗留键
        best: tuple[int, str] | None = None
        for key, aff in _LEGACY_CODE_TO_AFFINITY.items():
            if key in code_u or code_u in key:
                if best is None or len(key) > best[0]:
                    best = (len(key), aff)
        if best:
            return [best[1]]
        return []
    # 同亲和取最长词；多亲和都保留
    by_aff: dict[str, int] = {}
    for length, aff in hits:
        if aff not in by_aff or length > by_aff[aff]:
            by_aff[aff] = length
    return sorted(by_aff.keys(), key=lambda a: -by_aff[a])


def code_boost(option_code: str, mapping: dict[str, Decimal], option_name: str | None = None) -> Decimal:
    """对 mapping 求最大乘子。

    mapping 既可传亲和键（新），也可传旧英文码（兼容测试与遗留调用）。
    """
    if not mapping:
        return Decimal("1")
    # 若 mapping 仍是旧英文码表，先归一到亲和键
    norm_map: dict[str, Decimal] = {}
    for k, mult in mapping.items():
        key = str(k or "").strip()
        if not key:
            continue
        if key in _AFFINITY_SYNONYMS:
            aff = key
        else:
            aff = _LEGACY_CODE_TO_AFFINITY.get(key.upper(), key)
        prev = norm_map.get(aff)
        if prev is None or Decimal(str(mult)) > prev:
            norm_map[aff] = Decimal(str(mult))

    affinities = resolve_affinities(option_code, option_name)
    best = Decimal("1")
    for aff in affinities:
        m = norm_map.get(aff)
        if m is not None and m > best:
            best = m
    # 旧模糊：option_code 直接含子串键
    if best == Decimal("1"):
        code_u = (option_code or "").strip().upper()
        for key, mult in mapping.items():
            ku = str(key or "").strip().upper()
            if ku and (ku in code_u or code_u in ku):
                dm = Decimal(str(mult))
                if dm > best:
                    best = dm
    return best


def apply_scene_multiplier(
    base_score: float | Decimal,
    *,
    option_code: str,
    option_name: str | None = None,
    last_used_at: datetime | None = None,
    season: str,
    weather_condition: str | None = None,
    now: datetime | None = None,
) -> float:
    """读侧乘子，夹在合理范围后量化。"""
    score = Decimal(str(base_score or 0))
    mult = Decimal("1")
    mult *= code_boost(
        option_code,
        _SEASON_AFFINITY_BOOST.get(season, {}),
        option_name=option_name,
    )
    cond = (weather_condition or "").strip().lower() or None
    if cond:
        mult *= code_boost(
            option_code,
            _WEATHER_AFFINITY_BOOST.get(cond, {}),
            option_name=option_name,
        )
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


def weather_affinity_hint(condition: str | None) -> str | None:
    """当前天气对规格偏好的抬升说明；无 condition 返回 None。"""
    cond = (condition or "").strip().lower()
    if not cond:
        return None
    return _WEATHER_AFFINITY_HINT.get(cond)


def scene_summary_label(scene: dict[str, Any] | None) -> str | None:
    """后台/迎宾/小程序可读的场景摘要。"""
    if not scene:
        return None
    # enabled / scene_enabled 任一显式 False 则视为关闭
    if scene.get("enabled") is False or scene.get("scene_enabled") is False:
        return None
    parts: list[str] = []
    season = scene.get("season")
    if season:
        parts.append(_SEASON_LABELS.get(str(season), str(season)))
    cond = scene.get("condition")
    temp = scene.get("temp_c")
    if cond:
        label = _CONDITION_LABELS.get(str(cond), str(cond))
        if temp is not None:
            try:
                parts.append(f"{label} {float(temp):.0f}℃")
            except (TypeError, ValueError):
                parts.append(label)
        else:
            parts.append(label)
    elif scene.get("weather_active") is False and scene.get("weather_reason"):
        parts.append(str(scene["weather_reason"]))
    return " · ".join(parts) if parts else None


def _finalize_scene_label(ctx: dict[str, Any]) -> dict[str, Any]:
    ctx["label"] = scene_summary_label(
        {
            "enabled": bool(ctx.get("scene_enabled", True)),
            "scene_enabled": ctx.get("scene_enabled"),
            "season": ctx.get("season"),
            "condition": ctx.get("condition"),
            "temp_c": ctx.get("temp_c"),
            "weather_active": ctx.get("weather_active"),
            "weather_reason": ctx.get("weather_reason"),
        }
    )
    ctx["weather_affinity_hint"] = weather_affinity_hint(ctx.get("condition"))
    return ctx


async def build_scene_context(db, store_id: int | None = None) -> dict[str, Any]:
    """组装当前场景。

    - preference_scene_enabled：总开关（季节/同星期同餐段/天气乘子）
    - weather_pref_enabled：天气子开关（需门店经纬度；关则仅季节等仍可生效）
    """
    now = now_local()
    season = calendar_season(now)
    sid = int(store_id) if store_id else None
    ctx: dict[str, Any] = {
        "season": season,
        "weekday": int(now.weekday()),
        "hour": int(now.hour),
        "weather": None,
        "temp_c": None,
        "condition": None,
        "condition_label": None,
        "store_id": sid,
        "scene_enabled": True,
        "weather_pref_enabled": True,
        "weather_active": False,
        "weather_reason": None,
        "label": None,
        "weather_affinity_hint": None,
    }

    scene_on = True
    weather_on = True
    try:
        from app.services.system_settings_service import SystemSettingsService

        scene_on = await SystemSettingsService.effective_bool(
            db, "preference_scene_enabled", True, store_id=sid
        )
        weather_on = await SystemSettingsService.effective_bool(
            db, "weather_pref_enabled", True, store_id=sid
        )
    except Exception:
        scene_on = True
        weather_on = True

    ctx["scene_enabled"] = bool(scene_on)
    ctx["weather_pref_enabled"] = bool(weather_on)

    if not scene_on:
        ctx["weather_reason"] = "口味场景加权已关闭"
        return _finalize_scene_label(ctx)

    if not sid:
        ctx["weather_reason"] = "未指定门店"
        return _finalize_scene_label(ctx)

    if not weather_on:
        ctx["weather_reason"] = "天气口味加权已关闭"
        return _finalize_scene_label(ctx)

    try:
        from app.models import Store

        store = await db.get(Store, sid)
        lat = getattr(store, "latitude", None) if store else None
        lng = getattr(store, "longitude", None) if store else None
        if lat is None or lng is None:
            ctx["weather_reason"] = "门店未配置经纬度"
            return _finalize_scene_label(ctx)
        from app.services.weather_client import fetch_current_weather

        w = await fetch_current_weather(float(lat), float(lng))
        if not w:
            ctx["weather_reason"] = "天气服务暂不可用"
            return _finalize_scene_label(ctx)
        cond = str(w.get("condition") or "").strip().lower() or None
        ctx["weather"] = w
        ctx["temp_c"] = w.get("temp_c")
        ctx["condition"] = cond
        ctx["condition_label"] = w.get("condition_label") or (
            _CONDITION_LABELS.get(cond) if cond else None
        )
        ctx["weather_active"] = True
        ctx["weather_reason"] = None
    except Exception:
        ctx["weather_reason"] = "天气查询失败"
    return _finalize_scene_label(ctx)


def scene_ops_payload(ctx: dict[str, Any] | None) -> dict[str, Any]:
    """门店信息「探测天气口味」接口用的完整运维视图。"""
    from app.services.weather_client import weather_thresholds

    c = dict(ctx or {})
    return {
        **c,
        "thresholds": weather_thresholds(),
        "howto": (
            "1) 门店信息填写经纬度；2) 业务参数开启「口味场景加权」与「天气口味加权」；"
            "3) 规格偏好（迎宾/智能口味）与餐品推荐（猜你喜欢）读侧按季节+天气微调，不改写历史偏好。"
        ),
        "affinity_hints": dict(_WEATHER_AFFINITY_HINT),
    }

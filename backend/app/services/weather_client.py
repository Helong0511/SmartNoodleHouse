"""门店天气：Open-Meteo（免 Key），失败静默降级。

供「天气口味加权」读侧使用：condition 为 hot / cold / rainy / mild。
阈值与 preference_scene / recommendation_service 约定一致，改动须三处对齐。

缓存策略：
- 正常结果 TTL 1800s ±20% jitter（防雪崩）；
- 查询失败时写空值标记 60s（防穿透）；
- decode_responses=True 时 get 返回 str，无需手动 decode。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.core.async_utils import spawn_bg
from app.core.cache_keys import ttl_with_jitter, weather_key

logger = logging.getLogger(__name__)

_CACHE_TTL = 1800
_EMPTY_TTL = 60  # 空值标记 60s，防止 API 持续不可用时反复请求

# 气温阈值（℃）：≥热 / ≤冷；雨天优先于气温（WMO 降水码）
HOT_TEMP_C = 28.0
COLD_TEMP_C = 10.0

CONDITION_LABELS: dict[str, str] = {
    "hot": "炎热",
    "cold": "寒冷",
    "rainy": "雨天",
    "mild": "舒适",
}

# WMO Weather interpretation codes（降水/雷暴等）
_RAINY_WMO_CODES = frozenset(
    {
        51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99,
    }
)


def condition_from_wmo(code: int, temp_c: float) -> str:
    """粗分：hot / cold / rainy / mild。雨天优先于气温。"""
    if int(code) in _RAINY_WMO_CODES:
        return "rainy"
    t = float(temp_c)
    if t >= HOT_TEMP_C:
        return "hot"
    if t <= COLD_TEMP_C:
        return "cold"
    return "mild"


# 兼容旧内部名
_condition_from_wmo = condition_from_wmo


def weather_thresholds() -> dict[str, Any]:
    """运维探测 / 文档用的阈值摘要。"""
    return {
        "hot_temp_c": HOT_TEMP_C,
        "cold_temp_c": COLD_TEMP_C,
        "rainy_priority": True,
        "provider": "open-meteo",
        "cache_ttl_seconds": _CACHE_TTL,
        "empty_ttl_seconds": _EMPTY_TTL,
        "condition_labels": dict(CONDITION_LABELS),
    }


async def fetch_current_weather(lat: float, lng: float) -> dict[str, Any] | None:
    """返回 temp_c / weather_code / condition；不可用时 None。"""
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
        return None

    cache_key = weather_key(f"{lat_f:.2f}:{lng_f:.2f}")
    empty_key = f"{cache_key}:empty"
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        cached = await r.get(cache_key)
        if cached:
            return json.loads(cached)
        # 空值标记：防止 API 持续不可用时反复请求（缓存穿透）
        if await r.get(empty_key):
            return None
    except Exception:
        pass

    try:
        from app.core.http_client import get_http_client

        client = await get_http_client()
        resp = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat_f,
                "longitude": lng_f,
                "current": "temperature_2m,weather_code",
                "timezone": "Asia/Shanghai",
            },
        )
        resp.raise_for_status()
        data = resp.json() or {}
        cur = data.get("current") or {}
        temp = cur.get("temperature_2m")
        code = int(cur.get("weather_code") or 0)
        if temp is None:
            # API 返回但无温度数据：写空值标记防穿透
            _set_empty_marker(empty_key)
            return None
        cond = condition_from_wmo(code, float(temp))
        payload = {
            "temp_c": float(temp),
            "weather_code": code,
            "condition": cond,
            "condition_label": CONDITION_LABELS.get(cond, cond),
        }
    except Exception:
        logger.debug("天气查询失败 lat=%s lng=%s", lat_f, lng_f, exc_info=True)
        _set_empty_marker(empty_key)
        return None

    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        from app.core.response import dumps_json

        await r.set(cache_key, dumps_json(payload, compact=True), ex=ttl_with_jitter(_CACHE_TTL))
        await r.delete(empty_key)
    except Exception:
        pass
    return payload


def _set_empty_marker(empty_key: str) -> None:
    """写入空值标记（同步 fire-and-forget，不阻塞主流程）。"""
    async def _do_set():
        try:
            from app.core.redis_client import get_redis

            r = await get_redis()
            await r.set(empty_key, "1", ex=_EMPTY_TTL)
        except Exception:
            pass

    spawn_bg(_do_set())

"""门店天气：Open-Meteo（免 Key），失败静默降级。"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_TTL = 1800
_CACHE_PREFIX = "snh:weather:"


def _condition_from_wmo(code: int, temp_c: float) -> str:
    """粗分：hot / cold / rainy / mild。"""
    rainy_codes = {
        51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99,
    }
    if code in rainy_codes:
        return "rainy"
    if temp_c >= 28:
        return "hot"
    if temp_c <= 10:
        return "cold"
    return "mild"


async def fetch_current_weather(lat: float, lng: float) -> dict[str, Any] | None:
    """返回 temp_c / weather_code / condition；不可用时 None。"""
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
        return None

    cache_key = f"{_CACHE_PREFIX}{lat_f:.2f}:{lng_f:.2f}"
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        cached = await r.get(cache_key)
        if cached:
            raw = cached.decode() if isinstance(cached, (bytes, bytearray)) else str(cached)
            return json.loads(raw)
    except Exception:
        pass

    try:
        from app.core.http_client import get_http_client

        client = get_http_client()
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
            return None
        payload = {
            "temp_c": float(temp),
            "weather_code": code,
            "condition": _condition_from_wmo(code, float(temp)),
        }
    except Exception:
        logger.debug("天气查询失败 lat=%s lng=%s", lat_f, lng_f, exc_info=True)
        return None

    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        await r.set(cache_key, json.dumps(payload), ex=_CACHE_TTL)
    except Exception:
        pass
    return payload

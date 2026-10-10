"""门店天气：Open-Meteo（免 Key），失败静默降级。

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
from app.core.cache_keys import ttl_with_jitter

logger = logging.getLogger(__name__)

_CACHE_TTL = 1800
_EMPTY_TTL = 60  # 空值标记 60s，防止 API 持续不可用时反复请求
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
        payload = {
            "temp_c": float(temp),
            "weather_code": code,
            "condition": _condition_from_wmo(code, float(temp)),
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

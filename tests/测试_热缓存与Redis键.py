"""热缓存原语与 Redis 键目录标准化校验。"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from app.core.cache_keys import (
    FACE_LIVENESS_PREFIX,
    LEADER_KEY,
    MSG_DONE_PREFIX,
    ORDER_URGE_PREFIX,
    PAY_SYNC_PREFIX,
    PERF_ROUTES_SET_KEY,
    RATE_LIMIT_PREFIX,
    SYSTEM_SETTINGS_VER_KEY,
    TTS_WELCOME_QUEUE_KEY,
    WEBHOOK_REPLAY_PREFIX,
    WEATHER_PREFIX,
    WS_BROADCAST_CHANNEL,
    balance_notify_key,
    face_liveness_key,
    menu_bundle_key,
    menu_version_key,
    order_urge_key,
    pay_sync_key,
    perf_count_key,
    perf_samples_key,
    products_public_rate_limit_key,
    rate_limit_key,
    reco_key,
    reco_generation_key,
    ttl_with_jitter,
    weather_key,
    welcome_cooldown_key,
)
from app.core.hot_cache import HotCache, clear_all_hot_caches, list_hot_caches


def test_all_redis_keys_use_snh_prefix():
    keys = [
        LEADER_KEY,
        SYSTEM_SETTINGS_VER_KEY,
        TTS_WELCOME_QUEUE_KEY,
        PERF_ROUTES_SET_KEY,
        WS_BROADCAST_CHANNEL,
        MSG_DONE_PREFIX,
        WEBHOOK_REPLAY_PREFIX,
        WEATHER_PREFIX,
        RATE_LIMIT_PREFIX,
        ORDER_URGE_PREFIX,
        PAY_SYNC_PREFIX,
        FACE_LIVENESS_PREFIX,
        reco_key(1, 2, "home", "b0"),
        reco_generation_key(1),
        menu_bundle_key(1),
        menu_version_key(1),
        welcome_cooldown_key(9),
        face_liveness_key("tok"),
        rate_limit_key("1.1.1.1", "/x"),
        products_public_rate_limit_key("1.1.1.1", 1),
        order_urge_key(7),
        pay_sync_key(3),
        balance_notify_key(5),
        weather_key("31.20:121.50"),
        perf_samples_key("100", "/api/x"),
        perf_count_key("100", "/api/x"),
    ]
    for k in keys:
        assert k.startswith("snh:"), k


def test_ttl_with_jitter_bounds():
    for _ in range(40):
        v = ttl_with_jitter(100, min_ratio=0.8, max_ratio=1.2)
        assert 80 <= v <= 120


def test_hot_cache_lru_and_ttl(monkeypatch):
    clear_all_hot_caches()
    clock = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
    c = HotCache[str, int]("test.unit", max_size=2, ttl_seconds=10.0)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1
    c.put("c", 3)  # 淘汰最久未用 b
    assert c.get("b") is None
    assert c.get("a") == 1
    assert c.get("c") == 3
    clock["t"] = 1011.0  # 超过 TTL
    assert c.get("a") is None
    names = {s.name for s in list_hot_caches()}
    assert "test.unit" in names
    clear_all_hot_caches()
    assert len(c) == 0


def test_hot_cache_get_or_set():
    clear_all_hot_caches()
    c = HotCache[str, str]("test.factory", max_size=8)
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return "ok"

    assert c.get_or_set("k", factory) == "ok"
    assert c.get_or_set("k", factory) == "ok"
    assert calls["n"] == 1
    clear_all_hot_caches()


@pytest.mark.parametrize(
    "builder,args,expected",
    [
        (order_urge_key, (42,), "snh:order:urge:42"),
        (pay_sync_key, (9,), "snh:pay:sync:9"),
        (face_liveness_key, ("abc",), "snh:face:liveness:abc"),
        (weather_key, ("1.00:2.00",), "snh:weather:1.00:2.00"),
        (products_public_rate_limit_key, ("127.0.0.1", 1), "snh:rl:products:public:127.0.0.1:1"),
    ],
)
def test_key_builders_exact(builder, args, expected):
    assert builder(*args) == expected


@pytest.mark.asyncio
async def test_system_settings_cache_refresh_is_single_flight(monkeypatch):
    from app.services.system_settings_service import SystemSettingsService

    SystemSettingsService._cache = None
    SystemSettingsService._cache_ver = -1
    SystemSettingsService._cache_at = 0.0
    SystemSettingsService._cache_refresh_lock = asyncio.Lock()
    calls = {"count": 0}

    async def load_once(_db):
        calls["count"] += 1
        await asyncio.sleep(0.02)
        SystemSettingsService._cache = {"test_setting": "ok"}
        SystemSettingsService._cache_ver = 0
        SystemSettingsService._cache_at = time.monotonic()
        return SystemSettingsService._cache

    monkeypatch.setattr(
        SystemSettingsService, "_remote_version", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(SystemSettingsService, "_load_raw", AsyncMock(side_effect=load_once))
    db = object()

    await asyncio.gather(*(SystemSettingsService.touch_cache(db) for _ in range(5)))

    assert calls["count"] == 1
    assert SystemSettingsService._cache == {"test_setting": "ok"}
    # asyncio.Lock 可能绑定当前事件循环；测试结束前重置，避免跨 loop 污染。
    SystemSettingsService._cache_refresh_lock = asyncio.Lock()

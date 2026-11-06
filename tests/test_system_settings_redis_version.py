from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.execute_calls = 0

    async def execute(self, *_args, **_kwargs):
        self.execute_calls += 1
        return _Result(self.rows)


@pytest.mark.asyncio
async def test_remote_settings_version_missing_is_unknown_not_zero():
    from app.services.system_settings_service import (
        REDIS_CACHE_VER_KEY,
        SystemSettingsService as Service,
    )

    class Redis:
        async def get(self, key):
            assert key == REDIS_CACHE_VER_KEY
            return None

    async def get_redis():
        return Redis()

    with patch("app.core.redis_client.get_redis", get_redis):
        assert await Service._remote_version() is None


@pytest.mark.asyncio
async def test_remote_settings_version_redis_error_is_unknown():
    from app.services.system_settings_service import SystemSettingsService as Service

    with patch(
        "app.core.redis_client.get_redis",
        new_callable=AsyncMock,
        side_effect=RuntimeError("redis down"),
    ):
        assert await Service._remote_version() is None


@pytest.mark.asyncio
async def test_settings_cache_uses_only_bounded_stale_snapshot_when_version_unknown():
    from app.services.system_settings_service import SystemSettingsService as Service

    old_cache = Service._cache
    old_last = Service._last_db_cache
    old_at = Service._cache_at
    old_ver = Service._cache_ver
    try:
        Service._cache = {"trusted_proxy_ips": "10.0.0.1"}
        Service._last_db_cache = dict(Service._cache)
        Service._cache_at = time.monotonic() - 3
        Service._cache_ver = 27
        db = _DB([SimpleNamespace(setting_key="trusted_proxy_ips", setting_value="10.0.0.2")])

        with patch.object(Service, "_remote_version", new=AsyncMock(return_value=None)):
            result = await Service._load_raw(db)

        assert result["trusted_proxy_ips"] == "10.0.0.1"
        assert db.execute_calls == 0
        assert Service._cache_ver == -1

        Service._cache_at = time.monotonic() - Service.CACHE_TTL - 1
        result = await Service._load_raw(db)
        assert result["trusted_proxy_ips"] == "10.0.0.2"
        assert db.execute_calls == 1
        assert Service._cache_ver == -1
    finally:
        Service._cache = old_cache
        Service._last_db_cache = old_last
        Service._cache_at = old_at
        Service._cache_ver = old_ver


@pytest.mark.asyncio
async def test_settings_version_bump_is_atomic_increment_and_expire():
    from app.services.system_settings_service import (
        REDIS_CACHE_VER_KEY,
        SystemSettingsService as Service,
    )

    class Redis:
        def __init__(self):
            self.call = None

        async def eval(self, script, numkeys, key, ttl):
            self.call = (script, numkeys, key, ttl)
            return 28

    fake = Redis()

    async def get_redis():
        return fake

    with patch("app.core.redis_client.get_redis", get_redis):
        assert await Service._bump_remote_version() == 28

    script, numkeys, key, ttl = fake.call
    assert "INCR" in script
    assert "EXPIRE" in script
    assert numkeys == 1
    assert key == REDIS_CACHE_VER_KEY
    assert ttl == 30 * 24 * 3600


@pytest.mark.asyncio
async def test_direct_settings_loads_are_single_flight_across_sessions():
    import asyncio

    from app.services.system_settings_service import SystemSettingsService as Service

    old_cache = Service._cache
    old_last = Service._last_db_cache
    old_at = Service._cache_at
    old_ver = Service._cache_ver
    try:
        # 并发等待会绑定 asyncio.Lock 到当前测试事件循环；每个测试独立重置。
        Service._cache_refresh_lock = asyncio.Lock()
        Service._cache = None
        Service._last_db_cache = None
        Service._cache_at = 0.0
        Service._cache_ver = -1
        gate = asyncio.Event()
        calls = {"count": 0}

        class SlowDB(_DB):
            async def execute(self, *_args, **_kwargs):
                calls["count"] += 1
                await gate.wait()
                return _Result([SimpleNamespace(
                    setting_key="trusted_proxy_ips", setting_value="10.0.0.9"
                )])

        db1, db2 = SlowDB([]), SlowDB([])
        with patch.object(Service, "_remote_version", new=AsyncMock(return_value=None)):
            first = asyncio.create_task(Service._load_raw(db1))
            await asyncio.sleep(0)
            second = asyncio.create_task(Service._load_raw(db2))
            await asyncio.sleep(0)
            gate.set()
            result1, result2 = await asyncio.gather(first, second)

        assert calls["count"] == 1
        assert result1["trusted_proxy_ips"] == "10.0.0.9"
        assert result2["trusted_proxy_ips"] == "10.0.0.9"
    finally:
        Service._cache = old_cache
        Service._last_db_cache = old_last
        Service._cache_at = old_at
        Service._cache_ver = old_ver
        Service._cache_refresh_lock = asyncio.Lock()

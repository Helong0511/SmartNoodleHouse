from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core import menu_cache


@pytest.mark.asyncio
async def test_schedule_generation_is_persistent_and_changes_only_on_schedule_transition(monkeypatch):
    store_id = 77131
    schedule_key = menu_cache.menu_schedule_key(store_id)
    version_key = menu_cache.menu_version_key(store_id)
    bundle_key = menu_cache.menu_bundle_key(store_id)

    class FakeRedis:
        def __init__(self):
            self.store = {
                version_key: "8",
                schedule_key: "11",
                bundle_key: '{"version":8}',
            }

        async def eval(self, script, numkeys, actual_schedule_key, actual_version_key,
                       actual_bundle_key, desired_schedule):
            assert "-- MENU_SYNC_SCHEDULE_GENERATION" in script
            assert numkeys == 3
            assert (actual_schedule_key, actual_version_key, actual_bundle_key) == (
                schedule_key, version_key, bundle_key
            )
            current_schedule = self.store.get(actual_schedule_key)
            current_version = self.store.get(actual_version_key)
            if current_schedule == desired_schedule and current_version is not None:
                return current_version
            next_version = int(current_version or 0) + 1
            self.store[actual_version_key] = str(next_version)
            self.store[actual_schedule_key] = desired_schedule
            self.store.pop(actual_bundle_key, None)
            return next_version

    fake = FakeRedis()
    # Simulate Redis bundle TTL expiry while the persistent schedule/version keys remain.
    fake.store.pop(bundle_key, None)

    async def get_redis():
        return fake

    monkeypatch.setattr(menu_cache, "get_redis", get_redis)

    # Same active schedule is stable even after the short-lived bundle is absent.
    assert await menu_cache.sync_menu_schedule_generation(store_id, 11) == 8
    assert fake.store[version_key] == "8"

    # Crossing into another schedule increments the version and invalidates the bundle.
    assert await menu_cache.sync_menu_schedule_generation(store_id, 12) == 9
    assert fake.store[version_key] == "9"
    assert fake.store[schedule_key] == "12"
    assert bundle_key not in fake.store

    # Leaving all configured time windows is also a generation transition.
    assert await menu_cache.sync_menu_schedule_generation(store_id, None) == 10
    assert fake.store[schedule_key] == "none"


@pytest.mark.asyncio
async def test_schedule_generation_bootstraps_missing_marker_and_invalidates_old_bundle(monkeypatch):
    store_id = 77132
    schedule_key = menu_cache.menu_schedule_key(store_id)
    version_key = menu_cache.menu_version_key(store_id)
    bundle_key = menu_cache.menu_bundle_key(store_id)

    class FakeRedis:
        def __init__(self):
            self.store = {version_key: "21", bundle_key: '{"version":21}'}

        async def eval(self, script, numkeys, actual_schedule_key, actual_version_key,
                       actual_bundle_key, desired_schedule):
            assert self.store.get(actual_schedule_key) is None
            next_version = int(self.store[actual_version_key]) + 1
            self.store[actual_version_key] = str(next_version)
            self.store[actual_schedule_key] = desired_schedule
            self.store.pop(actual_bundle_key, None)
            return next_version

    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr(menu_cache, "get_redis", get_redis)

    assert await menu_cache.sync_menu_schedule_generation(store_id, 3) == 22
    assert fake.store[schedule_key] == "3"
    assert bundle_key not in fake.store


@pytest.mark.asyncio
async def test_schedule_generation_oom_fails_closed_without_advancing_marker(monkeypatch):
    store_id = 77133
    schedule_key = menu_cache.menu_schedule_key(store_id)
    version_key = menu_cache.menu_version_key(store_id)
    bundle_key = menu_cache.menu_bundle_key(store_id)

    class FakeRedis:
        def __init__(self):
            self.store = {
                version_key: "31",
                schedule_key: "5",
                bundle_key: '{"version":31}',
            }

        async def eval(self, *args):
            raise RuntimeError("OOM command not allowed when used memory > maxmemory")

    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr(menu_cache, "get_redis", get_redis)

    assert await menu_cache.sync_menu_schedule_generation(store_id, 6) is None
    assert fake.store[version_key] == "31"
    assert fake.store[schedule_key] == "5"
    assert fake.store[bundle_key] == '{"version":31}'


def test_local_store_cache_invalidation_does_not_require_redis(monkeypatch):
    from app.services import menu_service, recommendation_service

    store_id = 77134
    pool_delete = MagicMock()
    cf_delete = MagicMock()
    menu_delete = MagicMock()
    monkeypatch.setattr(recommendation_service, "_POOL_CACHE", SimpleNamespace(delete=pool_delete))
    monkeypatch.setattr(recommendation_service, "_CF_CACHE", SimpleNamespace(delete=cf_delete))
    monkeypatch.setattr(menu_service, "invalidate_local_menu_cache", menu_delete)

    recommendation_service.invalidate_store_local_caches(store_id)

    pool_delete.assert_called_once_with(store_id)
    cf_delete.assert_called_once_with(store_id)
    menu_delete.assert_called_once_with(store_id)

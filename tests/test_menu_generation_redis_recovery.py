from __future__ import annotations

import pytest

from app.core import menu_cache


@pytest.mark.asyncio
async def test_redis_total_data_loss_restores_version_from_mysql_generation(monkeypatch):
    store_id = 77191
    version_key = menu_cache.menu_version_key(store_id)
    bundle_key = menu_cache.menu_bundle_key(store_id)
    generation = 1_000_000_042

    class FakeRedis:
        def __init__(self):
            # Simulate Redis restart/restore with an empty dataset.
            self.values = {}

        async def eval(self, script, numkeys, actual_version_key, actual_bundle_key, desired):
            assert "-- MENU_ENSURE_DURABLE_GENERATION" in script
            assert numkeys == 2
            assert (actual_version_key, actual_bundle_key) == (version_key, bundle_key)
            current = self.values.get(actual_version_key)
            target = int(desired)
            if current is None or int(current) < target:
                self.values[actual_version_key] = str(target)
                self.values.pop(actual_bundle_key, None)
                return target
            if int(current) > target:
                return -1
            return int(current)

    redis = FakeRedis()

    async def get_redis():
        return redis

    monkeypatch.setattr(menu_cache, "get_redis", get_redis)

    assert await menu_cache.ensure_menu_generation(store_id, generation) == generation
    assert redis.values[version_key] == str(generation)

    # A second read after recovery is stable and does not create a new generation.
    assert await menu_cache.ensure_menu_generation(store_id, generation) == generation
    assert redis.values[version_key] == str(generation)


@pytest.mark.asyncio
async def test_stale_worker_cannot_regress_redis_generation(monkeypatch):
    store_id = 77192
    version_key = menu_cache.menu_version_key(store_id)
    bundle_key = menu_cache.menu_bundle_key(store_id)

    class FakeRedis:
        async def eval(self, script, numkeys, actual_version_key, actual_bundle_key, desired):
            assert actual_version_key == version_key
            assert actual_bundle_key == bundle_key
            assert int(desired) == 1_000_000_041
            return -1  # Redis already reflects a newer committed DB generation.

    async def get_redis():
        return FakeRedis()

    monkeypatch.setattr(menu_cache, "get_redis", get_redis)
    assert await menu_cache.ensure_menu_generation(store_id, 1_000_000_041) is None

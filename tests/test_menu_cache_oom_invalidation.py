"""Redis noeviction/OOM 时仍应尽力清理失效菜单 bundle。"""
from __future__ import annotations

import pytest

from app.core import menu_cache


@pytest.mark.asyncio
async def test_menu_version_oom_still_deletes_stale_bundle(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.deleted: list[str] = []

        async def incr(self, _key):
            raise RuntimeError("OOM command not allowed when used memory > maxmemory")

        async def delete(self, *keys):
            self.deleted.extend(keys)
            return len(keys)

    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr(menu_cache, "get_redis", get_redis)

    version = await menu_cache.bump_menu_version(88001)

    assert version == 0
    assert fake.deleted == [menu_cache.menu_bundle_key(88001)]


@pytest.mark.asyncio
async def test_menu_version_failure_and_redis_outage_remain_best_effort(monkeypatch):
    async def get_redis():
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(menu_cache, "get_redis", get_redis)

    # The cache layer must not turn a best-effort invalidation into an uncaught
    # exception on a write path; callers can observe 0 as invalidation failure.
    assert await menu_cache.bump_menu_version(88002) == 0

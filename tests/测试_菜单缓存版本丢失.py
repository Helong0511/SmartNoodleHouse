"""菜单 Redis 版本丢失时必须从 MySQL 持久代际恢复。"""
from __future__ import annotations

from unittest.mock import AsyncMock, ANY

import pytest


@pytest.mark.asyncio
async def test_menu_bundle_rebuilds_when_redis_version_key_is_missing(monkeypatch):
    from app.services import menu_cache_service, menu_service

    sid = 77123
    generation = 1_000_000_001
    stale = {
        "version": 9,
        "active_schedule_id": None,
        "categories": [],
        "products": {"items": [{"id": 1, "visibility_rule": "always"}], "total": 1},
    }
    fresh = {
        "active_schedule_id": None,
        "categories": [],
        "products": {"items": [{"id": 2, "visibility_rule": "always"}], "total": 1},
    }

    menu_service._BUILD_CACHE.delete(sid)
    monkeypatch.setattr(menu_service, "_active_schedule_id", AsyncMock(return_value=None))
    monkeypatch.setattr(
        menu_cache_service, "sync_store_menu_schedule_generation",
        AsyncMock(return_value=generation),
    )
    monkeypatch.setattr(menu_service, "ensure_menu_generation", AsyncMock(return_value=generation))
    monkeypatch.setattr(
        menu_service, "get_menu_version",
        AsyncMock(side_effect=[generation, generation, generation]),
    )
    monkeypatch.setattr(
        menu_service, "get_menu_bundle", AsyncMock(side_effect=[stale, stale])
    )
    monkeypatch.setattr(
        menu_service, "try_acquire_rebuild_lock", AsyncMock(return_value="owner-token")
    )
    monkeypatch.setattr(menu_service, "build_menu_bundle", AsyncMock(return_value=fresh))
    publish = AsyncMock(return_value=True)
    monkeypatch.setattr(menu_service, "set_menu_bundle", publish)
    monkeypatch.setattr(menu_service, "release_rebuild_lock", AsyncMock())

    result = await menu_service.get_or_build_menu_bundle(object(), sid)

    assert result["version"] == generation
    assert result["products"]["items"][0]["id"] == 2
    menu_service.build_menu_bundle.assert_awaited_once()
    publish.assert_awaited_once()
    assert publish.await_args.kwargs["expected_version"] == generation


@pytest.mark.asyncio
async def test_menu_bundle_does_not_serve_local_or_waited_cache_without_durable_generation(monkeypatch):
    from app.core.exceptions import AppException
    from app.services import menu_cache_service, menu_service

    sid = 77124
    stale = {
        "version": 4,
        "active_schedule_id": None,
        "categories": [],
        "products": {"items": [{"id": 1, "visibility_rule": "always"}], "total": 1},
    }

    menu_service._BUILD_CACHE.put(sid, stale)
    monkeypatch.setattr(menu_service, "_active_schedule_id", AsyncMock(return_value=None))
    monkeypatch.setattr(
        menu_cache_service, "sync_store_menu_schedule_generation",
        AsyncMock(return_value=1_000_000_001),
    )
    ensure = AsyncMock(return_value=None)
    monkeypatch.setattr(menu_service, "ensure_menu_generation", ensure)
    cached = AsyncMock()
    monkeypatch.setattr(menu_service, "get_menu_bundle", cached)
    build = AsyncMock()
    monkeypatch.setattr(menu_service, "build_menu_bundle", build)

    with pytest.raises(AppException) as exc_info:
        await menu_service.get_or_build_menu_bundle(object(), sid)

    assert exc_info.value.status_code == 503
    ensure.assert_awaited_once_with(sid, 1_000_000_001)
    cached.assert_not_awaited()
    build.assert_not_awaited()
    assert menu_service._BUILD_CACHE.get(sid) is None


@pytest.mark.asyncio
async def test_menu_cache_publish_refuses_missing_or_zero_version(monkeypatch):
    from app.core import menu_cache

    class FakeRedis:
        def __init__(self):
            self.store = {menu_cache.menu_rebuild_lock_key(77125): "owner-token"}

        async def eval(
            self, script, numkeys, lock_key, bundle_key, version_key,
            token, raw, ttl, expected_version,
        ):
            if self.store.get(lock_key) != token:
                return 0
            if self.store.get(version_key) != str(expected_version):
                return 0
            self.store[bundle_key] = raw
            return "OK"

    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr(menu_cache, "get_redis", get_redis)
    assert await menu_cache.set_menu_bundle(
        77125, {"products": []}, owner_token="owner-token", expected_version=0
    ) is False
    assert await menu_cache.set_menu_bundle(
        77125, {"products": []}, owner_token="owner-token", expected_version=1
    ) is False
    assert menu_cache.menu_bundle_key(77125) not in fake.store


@pytest.mark.asyncio
async def test_menu_bundle_fails_closed_when_durable_generation_cannot_be_mirrored(monkeypatch):
    from app.core.exceptions import AppException
    from app.services import menu_cache_service, menu_service

    sid = 77126
    menu_service._BUILD_CACHE.put(
        sid,
        {
            "version": 12,
            "active_schedule_id": 4,
            "categories": [],
            "products": {"items": [], "total": 0},
        },
    )
    monkeypatch.setattr(menu_service, "_active_schedule_id", AsyncMock(return_value=5))
    schedule_sync = AsyncMock(return_value=1_000_000_001)
    monkeypatch.setattr(
        menu_cache_service, "sync_store_menu_schedule_generation", schedule_sync
    )
    ensure = AsyncMock(return_value=None)
    monkeypatch.setattr(menu_service, "ensure_menu_generation", ensure)
    cached = AsyncMock()
    monkeypatch.setattr(menu_service, "get_menu_bundle", cached)

    with pytest.raises(AppException) as exc_info:
        await menu_service.get_or_build_menu_bundle(object(), sid)

    assert exc_info.value.status_code == 503
    schedule_sync.assert_awaited_once_with(ANY, sid, 5)
    ensure.assert_awaited_once_with(sid, 1_000_000_001)
    cached.assert_not_awaited()
    assert menu_service._BUILD_CACHE.get(sid) is None

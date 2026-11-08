"""菜单缓存版本键丢失时必须 fail-closed，不能把旧 bundle 当作有效缓存。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_menu_bundle_rebuilds_when_redis_version_key_is_missing(monkeypatch):
    from app.services import menu_service

    sid = 77123
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
        menu_service, "get_menu_bundle", AsyncMock(side_effect=[stale, stale])
    )
    # Redis version key is missing on both initial reads, then exists after bootstrap.
    monkeypatch.setattr(
        menu_service, "get_menu_version", AsyncMock(side_effect=[0, 0, 1, 1])
    )
    monkeypatch.setattr(menu_service, "bump_menu_version", AsyncMock(return_value=1))
    monkeypatch.setattr(
        menu_service, "try_acquire_rebuild_lock", AsyncMock(return_value="owner-token")
    )
    monkeypatch.setattr(menu_service, "build_menu_bundle", AsyncMock(return_value=fresh))
    publish = AsyncMock(return_value=True)
    monkeypatch.setattr(menu_service, "set_menu_bundle", publish)
    monkeypatch.setattr(menu_service, "release_rebuild_lock", AsyncMock())

    result = await menu_service.get_or_build_menu_bundle(object(), sid)

    assert result["version"] == 1
    assert result["products"]["items"][0]["id"] == 2
    menu_service.build_menu_bundle.assert_awaited_once()
    publish.assert_awaited_once()
    assert publish.await_args.kwargs["expected_version"] == 1


@pytest.mark.asyncio
async def test_menu_bundle_does_not_serve_local_or_waited_cache_without_version(monkeypatch):
    from app.core.exceptions import AppException
    from app.services import menu_service

    sid = 77124
    stale = {
        "version": 4,
        "active_schedule_id": None,
        "categories": [],
        "products": {"items": [{"id": 1, "visibility_rule": "always"}], "total": 1},
    }

    menu_service._BUILD_CACHE.put(sid, stale)
    monkeypatch.setattr(menu_service, "_active_schedule_id", AsyncMock(return_value=None))
    monkeypatch.setattr(menu_service, "get_menu_bundle", AsyncMock(return_value=None))
    monkeypatch.setattr(menu_service, "get_menu_version", AsyncMock(return_value=0))
    monkeypatch.setattr(
        menu_service, "try_acquire_rebuild_lock", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        menu_service, "wait_for_rebuild", AsyncMock(return_value=stale)
    )

    with pytest.raises(AppException) as exc_info:
        await menu_service.get_or_build_menu_bundle(object(), sid)

    # Redis outage and a missing version key are both untrusted states. The service
    # must not serve the process-local stale bundle or independently stampede MySQL.
    assert exc_info.value.status_code == 503
    menu_service.wait_for_rebuild.assert_awaited_once()
    menu_service.build_menu_bundle = AsyncMock()
    menu_service.build_menu_bundle.assert_not_awaited()
    assert menu_service._BUILD_CACHE.get(sid) is None


@pytest.mark.asyncio
async def test_menu_cache_publish_refuses_missing_or_zero_version(monkeypatch):
    from app.core import menu_cache

    class FakeRedis:
        def __init__(self):
            self.store = {
                menu_cache.menu_rebuild_lock_key(77125): "owner-token",
            }

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

    # Version 0 is never a valid published generation.
    assert await menu_cache.set_menu_bundle(
        77125, {"products": []}, owner_token="owner-token", expected_version=0
    ) is False
    # A positive expected version cannot be published if Redis evicted its version key.
    assert await menu_cache.set_menu_bundle(
        77125, {"products": []}, owner_token="owner-token", expected_version=1
    ) is False
    assert menu_cache.menu_bundle_key(77125) not in fake.store

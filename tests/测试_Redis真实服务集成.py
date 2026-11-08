"""真实 Redis 集成回归：验证菜单缓存 Lua fencing、互斥锁和版本失效。

本文件在本地未配置 REDIS_TEST_URL 时跳过；CI backend-tests 必须连接真实 redis-server。
FakeRedis 单测仍保留，用于快速覆盖边界；本测试负责验证真实 Redis Lua/TTL/并发语义。
"""
from __future__ import annotations

import asyncio
import os
import secrets

import pytest
from redis.asyncio import Redis


REDIS_TEST_URL = os.getenv("REDIS_TEST_URL")
pytestmark = pytest.mark.skipif(
    not REDIS_TEST_URL,
    reason="REDIS_TEST_URL 未配置，跳过真实 Redis 集成测试",
)


@pytest.mark.asyncio
async def test_real_redis_menu_cache_lock_fencing_and_version_invalidation(monkeypatch):
    from app.core import menu_cache

    redis_client = Redis.from_url(REDIS_TEST_URL, decode_responses=True, protocol=2)
    store_id = secrets.randbelow(2_000_000_000) + 1
    keys = (
        menu_cache.menu_rebuild_lock_key(store_id),
        menu_cache.menu_bundle_key(store_id),
        menu_cache.menu_version_key(store_id),
    )

    async def get_live_redis():
        return redis_client

    monkeypatch.setattr(menu_cache, "get_redis", get_live_redis)
    try:
        assert await redis_client.ping() is True
        server_info = await redis_client.info("server")
        assert str(server_info["redis_version"]).startswith("7.4."), server_info["redis_version"]

        # 多 worker 同时重建时，SET NX EX 必须只有一个 owner 成功。
        tokens = await asyncio.gather(
            *(menu_cache.try_acquire_rebuild_lock(store_id) for _ in range(24))
        )
        owners = [token for token in tokens if token]
        assert len(owners) == 1
        owner = owners[0]
        ttl = await redis_client.ttl(keys[0])
        assert 1 <= ttl <= menu_cache._REBUILD_LOCK_TTL

        await redis_client.set(keys[2], "1")
        bundle = {"version": 1, "categories": [{"id": 1, "name": "面食"}]}
        assert await menu_cache.set_menu_bundle(
            store_id, bundle, owner_token=owner, expected_version=1
        )
        assert await menu_cache.get_menu_bundle(store_id) == bundle

        # 锁过期/被接管后，旧 owner 不得释放新 owner 的锁，也不得覆盖缓存。
        new_owner = "new-owner-after-lease-takeover"
        await redis_client.set(keys[0], new_owner, ex=30)
        await menu_cache.release_rebuild_lock(store_id, owner_token=owner)
        assert await redis_client.get(keys[0]) == new_owner
        assert not await menu_cache.set_menu_bundle(
            store_id,
            {"version": 1, "categories": [{"id": 2, "name": "旧快照"}]},
            owner_token=owner,
            expected_version=1,
        )

        # 写路径版本递增必须使旧 bundle 失效，避免读到旧菜单快照。
        assert await menu_cache.bump_menu_version(store_id) == 2
        assert await redis_client.get(keys[2]) == "2"
        assert await redis_client.get(keys[1]) is None
        assert await menu_cache.get_menu_bundle(store_id) is None
    finally:
        await redis_client.delete(*keys)
        await redis_client.aclose()

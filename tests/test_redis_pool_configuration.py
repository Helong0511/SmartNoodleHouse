from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import redis_client


@pytest.mark.asyncio
async def test_redis_pool_uses_explicit_per_process_limit_and_socket_timeouts(monkeypatch):
    captured = {}

    class FakePool:
        pass

    class FakeClient:
        pass

    pool = FakePool()
    client = FakeClient()

    def make_pool(*args, **kwargs):
        captured["url"] = args[0]
        captured.update(kwargs)
        return pool

    monkeypatch.setattr(redis_client, "_redis", None)
    monkeypatch.setattr(redis_client, "_redis_pool", None)
    monkeypatch.setattr(redis_client, "get_settings", lambda: SimpleNamespace(
        redis_url="redis://cache.internal:6379/2",
        redis_max_connections=17,
    ))
    monkeypatch.setattr(redis_client.redis.BlockingConnectionPool, "from_url", make_pool)
    monkeypatch.setattr(redis_client.redis, "Redis", lambda **kwargs: client)

    result = await redis_client.get_redis()
    assert result is client
    assert captured["url"] == "redis://cache.internal:6379/2"
    assert captured["max_connections"] == 17
    assert captured["timeout"] == redis_client._REDIS_POOL_WAIT_TIMEOUT_SEC
    assert captured["socket_connect_timeout"] == 2.0
    assert captured["socket_timeout"] == 3.0
    assert captured["health_check_interval"] == 30
    assert captured["socket_keepalive"] is True
    assert captured["protocol"] == 2
    assert captured["legacy_responses"] is True
    assert captured["retry"].get_retries() == 0

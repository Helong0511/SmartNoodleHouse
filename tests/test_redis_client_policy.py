from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import redis_client


@pytest.mark.asyncio
async def test_redis_client_keeps_resp2_and_fail_fast_pool_policy(monkeypatch):
    # 只检查连接池配置，不建立网络连接；真实命令/Lua 行为由 Redis 集成测试覆盖。
    monkeypatch.setattr(redis_client, "_redis", None)
    monkeypatch.setattr(redis_client, "_redis_pool", None)
    monkeypatch.setattr(
        redis_client,
        "get_settings",
        lambda: SimpleNamespace(
            redis_url="redis://127.0.0.1:6379/15",
            redis_max_connections=17,
        ),
    )

    client = await redis_client.get_redis()
    try:
        pool = client.connection_pool

        assert pool.max_connections == 17
        assert pool.timeout == pytest.approx(0.75)
        assert pool.connection_kwargs["socket_connect_timeout"] == pytest.approx(2.0)
        assert pool.connection_kwargs["socket_timeout"] == pytest.approx(3.0)
        assert pool.connection_kwargs["protocol"] == 2
        assert pool.connection_kwargs["retry"].get_retries() == 0
        assert client.connection_pool.connection_kwargs["health_check_interval"] == 30
        assert client.legacy_responses is True
    finally:
        await redis_client.close_redis()

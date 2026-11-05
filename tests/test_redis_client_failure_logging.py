from __future__ import annotations

import logging

import pytest

from app.core import redis_client


@pytest.mark.asyncio
async def test_try_get_redis_throttles_repeated_outage_logs(monkeypatch, caplog):
    calls = iter([100.0, 101.0, 131.0])
    monkeypatch.setattr(redis_client.time, "monotonic", lambda: next(calls))

    async def fail_get_redis():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(redis_client, "get_redis", fail_get_redis)
    redis_client._last_redis_degrade_log_at = -redis_client._REDIS_DEGRADE_LOG_INTERVAL_SEC
    redis_client._suppressed_redis_degrade_logs = 0

    with caplog.at_level(logging.WARNING, logger="app.core.redis_client"):
        assert await redis_client.try_get_redis() is None
        assert await redis_client.try_get_redis() is None
        assert await redis_client.try_get_redis() is None

    records = [r for r in caplog.records if "Redis 不可用，进入降级" in r.message]
    assert len(records) == 2
    assert "抑制重复日志 1 次" in records[-1].message

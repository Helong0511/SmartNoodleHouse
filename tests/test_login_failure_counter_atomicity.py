from __future__ import annotations

import pytest

from app.api import auth


@pytest.mark.asyncio
async def test_login_failure_counter_uses_one_atomic_increment_and_expiry(monkeypatch):
    calls = []

    class FakeRedis:
        async def eval(self, script, numkeys, key, ttl):
            calls.append((script, numkeys, key, ttl))
            return 3

        async def set(self, key, value, *, ex):
            calls.append(("set", key, value, ex))

        async def delete(self, key):
            calls.append(("delete", key))

    fake = FakeRedis()

    async def fake_get_redis():
        return fake

    monkeypatch.setattr("app.core.redis_client.get_redis", fake_get_redis)
    monkeypatch.setattr(auth, "_login_fail_key", lambda username, ip: "login:fail:test")
    monkeypatch.setattr(auth, "_login_lock_key", lambda username, ip: "login:lock:test")

    await auth._record_login_failure("staff", "127.0.0.1")

    # Lua 原子计数后达到阈值：设置锁定键并删除失败计数键。
    assert len(calls) == 3
    script, numkeys, key, ttl = calls[0]
    assert "INCR" in script and "EXPIRE" in script
    assert numkeys == 1
    assert key == "login:fail:test"
    assert ttl == auth._LOGIN_FAIL_WINDOW
    assert calls[1] == ("set", "login:lock:test", "1", auth._LOGIN_LOCK_SECONDS)
    assert calls[2] == ("delete", "login:fail:test")

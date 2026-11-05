"""Leader 锁 Lua 原子性单测：验证 release/renew 不再误删/误续期他人的锁。"""
from __future__ import annotations

import pytest

from app.core import leader_lock


def _make_redis():
    """构造一个异步 fake redis，记录 eval/delete/expire 调用。"""
    calls: dict[str, list] = {"eval": [], "delete": [], "expire": [], "set": [], "get": []}

    class _Result:
        def __init__(self, value):
            self._value = value

        @property
        def rowcount(self):
            return self._value if isinstance(self._value, int) else 1

    class _FakeRedis:
        async def eval(self, script, numkeys, *args):
            calls["eval"].append((script, numkeys, args))
            # 返回 1 表示 compare 成功（DEL/EXPIRE 生效）
            return 1

        async def delete(self, key):
            calls["delete"].append(key)
            return 1

        async def expire(self, key, ttl):
            calls["expire"].append((key, ttl))
            return 1

        async def set(self, key, value, *, nx=None, ex=None):
            calls["set"].append((key, value, nx, ex))
            return True  # 假设总是获取成功

        async def get(self, key):
            calls["get"].append(key)
            return None

    return _FakeRedis(), calls


@pytest.mark.asyncio
async def test_release_uses_compare_and_delete_lua(monkeypatch):
    """release_leader 必须用 Lua compare-and-delete，禁止裸 DELETE。"""
    fake, calls = _make_redis()

    async def _get_redis():
        return fake

    monkeypatch.setattr(leader_lock, "get_redis", _get_redis)
    await leader_lock.release_leader("worker_A")
    assert len(calls["eval"]) == 1, "release 必须走 eval(compare-and-delete)"
    assert calls["eval"][0][1] == 1, "numkeys=1"
    assert "del" in calls["eval"][0][0].lower(), "脚本应包含 DEL"
    assert "get" in calls["eval"][0][0].lower(), "脚本应包含 GET 比较"
    assert len(calls["delete"]) == 0, "禁止裸 r.delete（TOCTOU 风险）"


@pytest.mark.asyncio
async def test_renew_uses_compare_and_expire_lua(monkeypatch):
    """try_acquire_leader 续期分支必须用 Lua compare-and-expire，禁止裸 EXPIRE。"""
    fake = _make_redis()[0]
    calls_delete = _make_redis()[1]

    # 构造一个 GET 返回 worker_id 相同的场景，触发续期分支
    class _FakeRedis2:
        def __init__(self):
            self.eval_calls: list = []
            self.expire_calls: list = []

        async def eval(self, script, numkeys, *args):
            self.eval_calls.append((script, numkeys, args))
            return 1  # compare 成功

        async def expire(self, key, ttl):
            self.expire_calls.append((key, ttl))
            return 1

        async def set(self, key, value, *, nx=None, ex=None):
            return None  # SETNX 失败，走续期路径

        async def get(self, key):
            return b"worker_A"  # 锁值 == worker_id，进入续期分支

    fake2 = _FakeRedis2()

    async def _get_redis():
        return fake2

    monkeypatch.setattr(leader_lock, "get_redis", _get_redis)
    held = await leader_lock.try_acquire_leader("worker_A", ttl=45)
    assert held is True
    assert len(fake2.eval_calls) == 1, "续期必须走 eval(compare-and-expire)"
    assert "expire" in fake2.eval_calls[0][0].lower()
    assert len(fake2.expire_calls) == 0, "禁止裸 r.expire（TOCTOU 风险）"


@pytest.mark.asyncio
async def test_release_does_not_delete_others_lock(monkeypatch):
    """A 的 release 在锁已被 B 抢占时不得删 B 的锁：eval 返回 0 即不删除。"""
    # 构造 eval 返回 0（compare 失败：锁值 != worker_A）
    class _FakeRedis3:
        def __init__(self):
            self.delete_called = False

        async def eval(self, script, numkeys, *args):
            return 0  # compare 失败

        async def delete(self, key):
            self.delete_called = True
            return 1

    fake3 = _FakeRedis3()

    async def _get_redis():
        return fake3

    monkeypatch.setattr(leader_lock, "get_redis", _get_redis)
    await leader_lock.release_leader("worker_A")
    assert not fake3.delete_called, "compare 失败时不得 DEL"



@pytest.mark.asyncio
async def test_renew_leader_lease_propagates_redis_connection_error(monkeypatch):
    async def _get_redis():
        raise ConnectionError("temporary redis outage")

    monkeypatch.setattr(leader_lock, "get_redis", _get_redis)
    with pytest.raises(ConnectionError):
        await leader_lock.renew_leader_lease("worker_A", ttl=45)


@pytest.mark.asyncio
async def test_renew_leader_lease_returns_false_only_when_owner_check_fails(monkeypatch):
    class _LostLeaseRedis:
        async def eval(self, script, numkeys, *args):
            assert "expire" in script.lower()
            return 0

    async def _get_redis():
        return _LostLeaseRedis()

    monkeypatch.setattr(leader_lock, "get_redis", _get_redis)
    leader_lock.set_leader_held(True)
    assert await leader_lock.renew_leader_lease("worker_A", ttl=45) is False
    assert leader_lock.is_leader_held() is False



@pytest.mark.asyncio
async def test_renew_loop_recovers_after_single_transient_redis_error(monkeypatch):
    calls = {"n": 0, "lost": 0}
    stop = __import__("asyncio").Event()

    async def _renew(_worker_id, *, ttl):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("temporary redis outage")
        leader_lock.set_leader_held(True)
        stop.set()
        return True

    async def _on_lost():
        calls["lost"] += 1

    from unittest.mock import AsyncMock

    from app.core import leader_fencing

    monkeypatch.setattr(leader_fencing, "confirm_database_epoch", AsyncMock(return_value=True))
    monkeypatch.setattr(leader_lock, "RENEW_INTERVAL", 0.001)
    monkeypatch.setattr(leader_lock, "renew_leader_lease", _renew)
    monkeypatch.setattr(leader_lock, "_on_lost", _on_lost)
    leader_lock.set_leader_held(True)

    await leader_lock.renew_leader_loop("worker_A", stop, ttl=45)

    assert calls["n"] == 2
    assert calls["lost"] == 0
    assert leader_lock.is_leader_held() is True

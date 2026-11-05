from __future__ import annotations

import pytest

from app.services import webhook_replay


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def eval(self, script, numkeys, key, owner_token):
        assert numkeys == 1
        if self.values.get(key) == owner_token:
            del self.values[key]
            return 1
        return 0


@pytest.mark.asyncio
async def test_old_callback_cannot_release_new_callback_claim(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(webhook_replay, "try_get_redis", lambda: _resolved(redis))

    old_owner = await webhook_replay.claim_once("pay:WECHAT", "receipt-1")
    assert old_owner

    # 模拟旧锁过期后，新回调成功认领同一个 receipt key。
    key = f"{webhook_replay.REDIS_NS}pay:WECHAT:receipt-1"
    redis.values.pop(key)
    new_owner = await webhook_replay.claim_once("pay:WECHAT", "receipt-1")
    assert new_owner and new_owner != old_owner

    await webhook_replay.release_claim("pay:WECHAT", "receipt-1", old_owner)
    assert redis.values[key] == new_owner

    await webhook_replay.release_claim("pay:WECHAT", "receipt-1", new_owner)
    assert key not in redis.values


@pytest.mark.asyncio
async def test_duplicate_claim_has_no_owner_and_cannot_release_existing_lock(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(webhook_replay, "try_get_redis", lambda: _resolved(redis))

    owner = await webhook_replay.claim_once("pay:ALIPAY", "receipt-2")
    duplicate = await webhook_replay.claim_once("pay:ALIPAY", "receipt-2")
    assert owner
    assert duplicate is None

    await webhook_replay.release_claim("pay:ALIPAY", "receipt-2", duplicate)
    key = f"{webhook_replay.REDIS_NS}pay:ALIPAY:receipt-2"
    assert redis.values[key] == owner


@pytest.mark.asyncio
async def test_redis_outage_fails_open_with_unowned_release_noop(monkeypatch):
    async def unavailable():
        return None

    monkeypatch.setattr(webhook_replay, "try_get_redis", unavailable)
    owner = await webhook_replay.claim_once("pay:WECHAT", "receipt-3")
    assert owner
    await webhook_replay.release_claim("pay:WECHAT", "receipt-3", owner)


async def _resolved(value):
    return value

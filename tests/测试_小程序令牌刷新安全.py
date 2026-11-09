from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.miniprogram_auth import _allow_explicit_mock_code
from app.api.miniprogram_auth import (
    _consume_refresh_rotation,
    _validated_customer_channel,
)
from app.core.cache_keys import customer_refresh_rotation_key


def test_refresh_rotation_key_isolated_by_jti():
    assert customer_refresh_rotation_key(7, "jti-a") != customer_refresh_rotation_key(7, "jti-b")
    assert customer_refresh_rotation_key(7, "jti-a") != customer_refresh_rotation_key(8, "jti-a")


@pytest.mark.asyncio
async def test_refresh_rotation_uses_jti_and_covers_token_expiry_plus_grace(monkeypatch):
    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=True)
    monkeypatch.setattr("app.api.miniprogram_auth.try_get_redis", AsyncMock(return_value=fake_redis))
    monkeypatch.setattr("app.api.miniprogram_auth.time.time", lambda: 1_000)

    ok = await _consume_refresh_rotation(
        customer_id=7,
        token_exp=2_000,
        grace_hours=24,
        token_jti="old-token-jti",
    )

    assert ok is True
    fake_redis.set.assert_awaited_once_with(
        customer_refresh_rotation_key(7, "old-token-jti"),
        "1",
        nx=True,
        ex=2_000 - 1_000 + 24 * 3600,
    )


@pytest.mark.asyncio
async def test_refresh_rotation_rejects_replay(monkeypatch):
    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=None)
    monkeypatch.setattr("app.api.miniprogram_auth.try_get_redis", AsyncMock(return_value=fake_redis))

    ok = await _consume_refresh_rotation(7, 2_000, 24, "same-jti")

    assert ok is False


@pytest.mark.asyncio
async def test_refresh_rotation_fails_closed_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr("app.api.miniprogram_auth.try_get_redis", AsyncMock(return_value=None))

    ok = await _consume_refresh_rotation(7, 2_000, 24, "old-jti")

    assert ok is False


@pytest.mark.asyncio
async def test_refresh_rotation_fails_closed_when_redis_errors(monkeypatch):
    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(side_effect=RuntimeError("redis unavailable"))
    monkeypatch.setattr("app.api.miniprogram_auth.try_get_redis", AsyncMock(return_value=fake_redis))

    ok = await _consume_refresh_rotation(7, 2_000, 24, "old-jti")

    assert ok is False


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"channel": "alipay"}, "alipay"),
        ({"channel": "wechat"}, "wechat"),
        ({"channel": "ALIPAY"}, "alipay"),
        ({"channel": "unknown"}, "wechat"),
        ({}, "wechat"),
    ],
)
def test_refresh_preserves_validated_channel_from_expired_token(payload, expected):
    assert _validated_customer_channel(payload) == expected


def test_explicit_mock_login_allowed_only_in_development(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(
            is_development=True,
            is_production=False,
            app_debug=True,
            allow_auth_mock=True,
        ),
    )
    assert _allow_explicit_mock_code("mock:local-device") is True

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(
            is_development=False,
            is_production=False,
            app_debug=True,
            allow_auth_mock=True,
        ),
    )
    assert _allow_explicit_mock_code("mock:staging-device") is False

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(
            is_development=False,
            is_production=True,
            app_debug=True,
            allow_auth_mock=True,
        ),
    )
    assert _allow_explicit_mock_code("mock:production-device") is False

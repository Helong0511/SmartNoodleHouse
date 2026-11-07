"""Regression tests for owner-safe WeChat phone-code replay claims."""
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.exceptions import AppException


@pytest.mark.asyncio
async def test_phone_code_local_duplicate_releases_redis_claim_with_owner_token(monkeypatch):
    from app.services import webhook_replay, wechat_mini_client

    code = "wx-phone-code-duplicate"
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    claim = AsyncMock(return_value="owner-token-1")
    release = AsyncMock()
    monkeypatch.setattr(webhook_replay, "claim_once", claim)
    monkeypatch.setattr(webhook_replay, "release_claim", release)
    monkeypatch.setattr(wechat_mini_client, "_claim_phone_code_local", lambda _: False)

    with pytest.raises(AppException, match="授权码已使用"):
        await wechat_mini_client.get_phone_by_code(code)

    claim.assert_awaited_once_with("wx_phone_code", digest, ttl=600)
    release.assert_awaited_once_with("wx_phone_code", digest, "owner-token-1")


@pytest.mark.asyncio
async def test_phone_code_configuration_failure_releases_only_its_redis_claim(monkeypatch):
    from app.services import webhook_replay, wechat_mini_client

    code = "wx-phone-code-provider-failure"
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    claim = AsyncMock(return_value="owner-token-2")
    release = AsyncMock()
    local_release = Mock()
    monkeypatch.setattr(webhook_replay, "claim_once", claim)
    monkeypatch.setattr(webhook_replay, "release_claim", release)
    monkeypatch.setattr(wechat_mini_client, "_claim_phone_code_local", lambda _: True)
    monkeypatch.setattr(wechat_mini_client, "_release_phone_code_local", local_release)
    monkeypatch.setattr(
        wechat_mini_client,
        "get_settings",
        lambda: SimpleNamespace(wechat_app_id="", wechat_app_secret=""),
    )
    monkeypatch.setattr(wechat_mini_client, "_mock_allowed", lambda: False)

    with pytest.raises(AppException, match="微信小程序未配置"):
        await wechat_mini_client.get_phone_by_code(code)

    release.assert_awaited_once_with("wx_phone_code", digest, "owner-token-2")
    local_release.assert_called_once_with(digest)



@pytest.mark.asyncio
async def test_phone_code_transport_failure_releases_only_its_redis_claim(monkeypatch):
    import httpx
    from app.services import webhook_replay, wechat_mini_client

    code = "wx-phone-code-transport-failure"
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    claim = AsyncMock(return_value="owner-token-3")
    release = AsyncMock()
    local_release = Mock()

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("connection reset")

    monkeypatch.setattr(webhook_replay, "claim_once", claim)
    monkeypatch.setattr(webhook_replay, "release_claim", release)
    monkeypatch.setattr(wechat_mini_client, "_claim_phone_code_local", lambda _: True)
    monkeypatch.setattr(wechat_mini_client, "_release_phone_code_local", local_release)
    monkeypatch.setattr(
        wechat_mini_client,
        "get_settings",
        lambda: SimpleNamespace(wechat_app_id="wx-test", wechat_app_secret="secret"),
    )
    monkeypatch.setattr(wechat_mini_client, "_mock_allowed", lambda: False)
    monkeypatch.setattr(
        wechat_mini_client,
        "get_access_token",
        AsyncMock(return_value="access-token"),
    )
    monkeypatch.setattr(
        wechat_mini_client.httpx,
        "AsyncClient",
        lambda **kwargs: FailingClient(),
    )

    with pytest.raises(httpx.ConnectError, match="connection reset"):
        await wechat_mini_client.get_phone_by_code(code)

    release.assert_awaited_once_with("wx_phone_code", digest, "owner-token-3")
    local_release.assert_called_once_with(digest)

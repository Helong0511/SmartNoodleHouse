"""Regression tests for fail-closed mini-program mock and public QR-token handling."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from app.core.exceptions import AppException


def _request(path: str = "/api/miniprogram/entry") -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 1234),
        "scheme": "http",
    })


@pytest.mark.parametrize(
    ("app_debug", "allow_auth_mock", "expected"),
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, True),
    ],
)
def test_dev_mode_mock_requires_all_three_gates(monkeypatch, app_debug, allow_auth_mock, expected):
    from app.core import config

    settings = SimpleNamespace(
        is_development=True,
        app_debug=app_debug,
        allow_auth_mock=allow_auth_mock,
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    assert config.dev_mode_allowed("allow_auth_mock") is expected


def test_mock_login_prefix_is_rejected_without_explicit_debug_gate(monkeypatch):
    from app.core import config
    from app.services.miniprogram_auth import _allow_explicit_mock_code

    settings = SimpleNamespace(
        is_development=True,
        app_debug=True,
        allow_auth_mock=False,
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    assert _allow_explicit_mock_code("mock:simulator") is False


def test_alipay_plaintext_phone_never_counts_as_official_authorization(monkeypatch):
    from app.services.alipay_mini_client import decrypt_phone_response

    settings = SimpleNamespace(
        is_development=True,
        app_debug=True,
        allow_auth_mock=True,
        alipay_aes_key="",
    )
    monkeypatch.setattr("app.services.alipay_mini_client.get_settings", lambda: settings)
    with pytest.raises(AppException, match="AES 加密报文"):
        decrypt_phone_response('{"code":"10000","mobile":"13800138000"}')


@pytest.mark.asyncio
async def test_wechat_missing_provider_credentials_never_fabricate_verified_phone(monkeypatch):
    from app.services import webhook_replay, wechat_mini_client

    monkeypatch.setattr(webhook_replay, "claim_once", AsyncMock(return_value="owner"))
    monkeypatch.setattr(webhook_replay, "release_claim", AsyncMock())
    monkeypatch.setattr(wechat_mini_client, "_claim_phone_code_local", lambda _: True)
    monkeypatch.setattr(wechat_mini_client, "_release_phone_code_local", lambda _: None)
    monkeypatch.setattr(
        wechat_mini_client,
        "get_settings",
        lambda: SimpleNamespace(wechat_app_id="", wechat_app_secret=""),
    )
    monkeypatch.setattr(wechat_mini_client, "_mock_allowed", lambda: True)

    with pytest.raises(AppException, match="未配置"):
        await wechat_mini_client.get_phone_by_code("synthetic-code")


@pytest.mark.asyncio
async def test_public_scan_entry_does_not_return_permanent_qr_token(monkeypatch):
    from app.api import miniprogram_dine

    table = SimpleNamespace(
        id=12,
        table_no="A01",
        table_name="1号桌",
        store_id=3,
        qr_token="permanent-secret-token",
    )
    monkeypatch.setattr(
        "app.services.table_resolve.resolve_dining_table",
        AsyncMock(return_value=table),
    )
    monkeypatch.setattr(miniprogram_dine, "store_brief", AsyncMock(return_value={"id": 3, "name": "测试门店"}))

    response = await miniprogram_dine.scan_entry(
        _request(),
        SimpleNamespace(),
        token="scanned-token",
        channel="wechat",
    )
    assert response["data"]["table"]["id"] == 12
    assert "qr_token" not in response["data"]["table"]
    assert "permanent-secret-token" not in repr(response)


@pytest.mark.asyncio
async def test_production_cannot_disable_face_liveness_challenge():
    from app.api import miniprogram_face

    settings = SimpleNamespace(face_liveness_mode="none", is_production=True)
    with patch.object(miniprogram_face, "get_settings", return_value=settings):
        with pytest.raises(AppException) as exc:
            await miniprogram_face._enforce_liveness_challenge(
                7, liveness_token=None, action_completed=False
            )
    assert exc.value.code == 40342

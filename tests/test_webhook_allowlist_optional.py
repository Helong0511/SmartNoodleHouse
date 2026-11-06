from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.responses import Response

from app.core import ip_allowlist


def _request():
    return SimpleNamespace(
        url=SimpleNamespace(path="/api/payments/wechat/notify"),
        headers={},
        client=None,
    )


@pytest.mark.asyncio
async def test_production_webhook_without_allowlist_can_pass_only_when_opted_out(monkeypatch):
    from app.services.system_settings_service import SystemSettingsService

    settings = SimpleNamespace(
        is_production=True,
        webhook_ip_allowlist="",
        webhook_require_allowlist=False,
    )
    monkeypatch.setattr(ip_allowlist, "get_settings", lambda: settings)
    monkeypatch.setattr(SystemSettingsService, "touch_cache", AsyncMock())
    monkeypatch.setattr(SystemSettingsService, "resolved_str_sync", MagicMock(return_value=""))
    monkeypatch.setattr(SystemSettingsService, "resolved_bool_sync", MagicMock(return_value=False))

    expected = Response(content=b"verified-by-handler")
    call_next = AsyncMock(return_value=expected)
    middleware = ip_allowlist.WebhookAllowlistMiddleware(app=MagicMock())

    result = await middleware.dispatch(_request(), call_next)

    assert result is expected
    call_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_production_webhook_empty_allowlist_fails_closed_when_required(monkeypatch):
    from app.services.system_settings_service import SystemSettingsService

    settings = SimpleNamespace(
        is_production=True,
        webhook_ip_allowlist="",
        webhook_require_allowlist=True,
    )
    monkeypatch.setattr(ip_allowlist, "get_settings", lambda: settings)
    monkeypatch.setattr(SystemSettingsService, "touch_cache", AsyncMock())
    monkeypatch.setattr(SystemSettingsService, "resolved_str_sync", MagicMock(return_value=""))
    monkeypatch.setattr(SystemSettingsService, "resolved_bool_sync", MagicMock(return_value=True))
    monkeypatch.setattr(
        ip_allowlist,
        "json_error_response",
        lambda request, *, status_code, code, message: Response(
            content=message.encode(), status_code=status_code
        ),
    )

    call_next = AsyncMock(return_value=Response(content=b"must-not-run"))
    middleware = ip_allowlist.WebhookAllowlistMiddleware(app=MagicMock())

    result = await middleware.dispatch(_request(), call_next)

    assert result.status_code == 403
    call_next.assert_not_awaited()

"""限流降级：普通路径 Redis 宕机 fail-open；敏感路径可 fail-closed。

设计权衡：
- 默认 fail-open 避免 Redis 故障扩大为全站 503。
- RATE_LIMIT_FAIL_CLOSED_PATHS 仅对登录/支付等敏感前缀返回 503，降低撞库窗口。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request


def _request(path: str) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("10.0.0.2", 12345),
        "server": ("test", 80),
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_non_sensitive_path_fail_open_on_redis_error():
    from app.core.rate_limit import RateLimitMiddleware

    mw = RateLimitMiddleware(app=MagicMock())
    request = _request("/api/orders")
    settings = SimpleNamespace(
        is_production=True,
        is_development=False,
        enable_rate_limit=True,
        rate_limit_fail_closed_paths="/api/auth/login,/api/payments/",
    )
    next_call = AsyncMock(return_value=MagicMock(status_code=200))
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        with patch("app.core.rate_limit.get_redis", AsyncMock(side_effect=RuntimeError("redis down"))):
            with patch("app.core.rate_limit.client_ip", return_value="10.0.0.2"):
                resp = await mw.dispatch(request, next_call)
    assert resp is next_call.return_value
    next_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_sensitive_path_fail_closed_on_redis_error():
    from app.core.rate_limit import RateLimitMiddleware

    mw = RateLimitMiddleware(app=MagicMock())
    request = _request("/api/auth/login")
    settings = SimpleNamespace(
        is_production=True,
        is_development=False,
        enable_rate_limit=True,
        rate_limit_fail_closed_paths="/api/auth/login,/api/payments/",
    )
    next_call = AsyncMock(return_value=MagicMock(status_code=200))
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        with patch("app.core.rate_limit.get_redis", AsyncMock(side_effect=RuntimeError("redis down"))):
            with patch("app.core.rate_limit.client_ip", return_value="10.0.0.2"):
                resp = await mw.dispatch(request, next_call)
    assert resp.status_code == 503
    body = resp.body.decode() if hasattr(resp, "body") else ""
    assert "50301" in body or "限流" in body
    next_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_payments_prefix_fail_closed_on_redis_error():
    from app.core.rate_limit import RateLimitMiddleware

    mw = RateLimitMiddleware(app=MagicMock())
    request = _request("/api/payments/create")
    settings = SimpleNamespace(
        is_production=True,
        is_development=False,
        enable_rate_limit=True,
        rate_limit_fail_closed_paths="/api/auth/login,/api/payments/",
    )
    next_call = AsyncMock(return_value=MagicMock(status_code=200))
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        with patch("app.core.rate_limit.get_redis", AsyncMock(side_effect=RuntimeError("redis down"))):
            with patch("app.core.rate_limit.client_ip", return_value="10.0.0.2"):
                resp = await mw.dispatch(request, next_call)
    assert resp.status_code == 503
    next_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_token_bucket_denial_returns_retry_after(monkeypatch):
    from app.core.rate_limit import RateLimitMiddleware

    redis_client = MagicMock()
    redis_client.eval = AsyncMock(return_value=[0, 2500])
    mw = RateLimitMiddleware(app=MagicMock())
    request = _request("/api/auth/login")
    settings = SimpleNamespace(
        is_production=True, is_development=False, enable_rate_limit=True,
        rate_limit_fail_closed_paths="/api/auth/login",
    )
    next_call = AsyncMock(return_value=MagicMock(status_code=200))
    with patch("app.services.system_settings_service.SystemSettingsService.touch_cache", AsyncMock()), patch(
        "app.core.rate_limit.get_settings", return_value=settings
    ), patch(
        "app.core.rate_limit.get_redis", AsyncMock(return_value=redis_client)
    ), patch(
        "app.core.rate_limit.client_ip", return_value="10.0.0.2"
    ):
        response = await mw.dispatch(request, next_call)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3"
    redis_client.eval.assert_awaited_once()
    args = redis_client.eval.await_args.args
    assert "HSET" in args[0] and "PEXPIRE" in args[0]
    assert args[3] == 10
    assert args[4] == 60000
    assert str(args[2]).endswith(":tb2")
    next_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_token_bucket_allowed_request_continues(monkeypatch):
    from app.core.rate_limit import RateLimitMiddleware

    redis_client = MagicMock()
    redis_client.eval = AsyncMock(return_value=[1, 0])
    mw = RateLimitMiddleware(app=MagicMock())
    request = _request("/api/orders")
    settings = SimpleNamespace(
        is_production=True, is_development=False, enable_rate_limit=True,
        rate_limit_fail_closed_paths="",
    )
    expected = MagicMock(status_code=200)
    next_call = AsyncMock(return_value=expected)
    with patch("app.services.system_settings_service.SystemSettingsService.touch_cache", AsyncMock()), patch(
        "app.core.rate_limit.get_settings", return_value=settings
    ), patch(
        "app.core.rate_limit.get_redis", AsyncMock(return_value=redis_client)
    ), patch(
        "app.core.rate_limit.client_ip", return_value="10.0.0.2"
    ):
        response = await mw.dispatch(request, next_call)

    assert response is expected
    next_call.assert_awaited_once()


def test_production_required_rate_limit_paths_are_covered():
    from app.core.startup_checks import _missing_rate_limit_fail_closed_paths

    assert _missing_rate_limit_fail_closed_paths(
        "/api/auth/login,/api/auth/verify-password,/api/payments/,"
        "/api/miniprogram/wechat/login,/api/miniprogram/alipay/login,"
        "/api/miniprogram/auth/refresh,/api/miniprogram/member/phone,"
        "/api/delivery/meituan/notify,"
        "/api/delivery/eleme/notify"
    ) == []
    assert _missing_rate_limit_fail_closed_paths("/api/auth/login,/api/payments/") == [
        "/api/auth/verify-password",
        "/api/miniprogram/wechat/login",
        "/api/miniprogram/alipay/login",
        "/api/miniprogram/auth/refresh",
        "/api/miniprogram/member/phone",
        "/api/delivery/meituan/notify",
        "/api/delivery/eleme/notify",
    ]


def test_parent_prefix_covers_sensitive_rate_limit_paths():
    from app.core.startup_checks import _missing_rate_limit_fail_closed_paths

    assert _missing_rate_limit_fail_closed_paths("/api") == []
    assert _missing_rate_limit_fail_closed_paths("") == [
        "/api/auth/login",
        "/api/auth/verify-password",
        "/api/payments/",
        "/api/miniprogram/wechat/login",
        "/api/miniprogram/alipay/login",
        "/api/miniprogram/auth/refresh",
        "/api/miniprogram/member/phone",
        "/api/delivery/meituan/notify",
        "/api/delivery/eleme/notify",
    ]



@pytest.mark.asyncio
async def test_phone_binding_path_fail_closed_on_redis_error():
    from app.core.rate_limit import RateLimitMiddleware

    mw = RateLimitMiddleware(app=MagicMock())
    request = _request("/api/miniprogram/member/phone")
    settings = SimpleNamespace(
        is_production=True,
        is_development=False,
        enable_rate_limit=True,
        rate_limit_fail_closed_paths="/api/miniprogram/member/phone",
    )
    next_call = AsyncMock(return_value=MagicMock(status_code=200))
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        with patch("app.core.rate_limit.get_redis", AsyncMock(side_effect=RuntimeError("redis down"))):
            with patch("app.core.rate_limit.client_ip", return_value="10.0.0.2"):
                resp = await mw.dispatch(request, next_call)
    assert resp.status_code == 503
    next_call.assert_not_awaited()

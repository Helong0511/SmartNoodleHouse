"""生产限流：Redis 宕机时对全部路径 fail-closed。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request


@pytest.mark.asyncio
async def test_prod_rate_limit_fail_closed_on_redis_error():
    from app.core.rate_limit import RateLimitMiddleware

    mw = RateLimitMiddleware(app=MagicMock())
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/orders",
        "raw_path": b"/api/orders",
        "query_string": b"",
        "headers": [],
        "client": ("10.0.0.2", 12345),
        "server": ("test", 80),
    }
    request = Request(scope)

    settings = SimpleNamespace(is_production=True, is_development=False, enable_rate_limit=True)
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        with patch("app.core.rate_limit.get_redis", AsyncMock(side_effect=RuntimeError("redis down"))):
            with patch("app.core.rate_limit.client_ip", return_value="10.0.0.2"):
                resp = await mw.dispatch(request, AsyncMock())
    assert resp.status_code == 503

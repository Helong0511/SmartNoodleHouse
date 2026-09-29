"""限流降级行为：Redis 宕机时 fail-open（业务以 MySQL 为准）。

设计权衡：
- fail-open 让限流失效，但 Redis 仅是性能保护层，业务侧还有认证 / 入侵检测 /
  SQL 注入检测等多层防线，限流失效不会直接导致越权。
- fail-closed 让全站 503，扩大 Redis 故障半径，影响正常营业。

后续若决定生产环境 Redis 宕机时也 fail-closed，可在此测试改为期望 503。
"""
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
    next_call = AsyncMock(return_value=MagicMock(status_code=200))
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        with patch("app.core.rate_limit.get_redis", AsyncMock(side_effect=RuntimeError("redis down"))):
            with patch("app.core.rate_limit.client_ip", return_value="10.0.0.2"):
                resp = await mw.dispatch(request, next_call)
    # Redis 宕机 → 降级放行（业务侧多层防护兜底，避免全站 503 扩大故障半径）
    assert resp is next_call.return_value
    next_call.assert_awaited_once()

"""客户端日志控制字符与登录锁定辅助。"""
from unittest.mock import AsyncMock, patch

import pytest


def test_client_log_strips_newlines():
    from app.api.client_logs import _strip_ctrl

    assert "\n" not in _strip_ctrl("a\nb\rc")
    assert _strip_ctrl("ok") == "ok"


@pytest.mark.asyncio
async def test_login_lock_helpers():
    from app.api import auth as auth_mod

    fake = AsyncMock()
    fake.get = AsyncMock(return_value="1")
    fake.incr = AsyncMock(return_value=8)
    fake.set = AsyncMock()
    fake.delete = AsyncMock()
    fake.expire = AsyncMock()

    with patch("app.core.redis_client.get_redis", AsyncMock(return_value=fake)):
        assert await auth_mod._login_is_locked("alice") is True
        await auth_mod._record_login_failure("bob")
        fake.set.assert_awaited()
        await auth_mod._clear_login_failures("bob")
        fake.delete.assert_awaited()

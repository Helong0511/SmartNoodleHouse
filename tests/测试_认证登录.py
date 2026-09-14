"""员工登录最近登录时间"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.v1.auth import login
from app.schemas.common import LoginRequest


@pytest.mark.asyncio
async def test_login_updates_last_login_at():
    role = MagicMock()
    role.code = "SUPER_ADMIN"
    user = MagicMock()
    user.id = 1
    user.username = "admin"
    user.password_hash = "hash"
    user.display_name = "Admin"
    user.role_id = 1
    user.store_id = None
    user.role = role
    user.token_version = 0
    user.last_login_at = None

    db = MagicMock()
    row = MagicMock()
    row.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=row)
    db.flush = AsyncMock()

    req = MagicMock()
    data = LoginRequest(username="admin", password="secret")

    with patch("app.api.v1.auth.verify_password", return_value=True), patch(
        "app.api.v1.auth.create_access_token", return_value="tok"
    ), patch("app.api.deps.get_user_permissions", AsyncMock(return_value={"order:read"})):
        resp = await login(data, req, db)

    assert resp["code"] == 0
    assert user.last_login_at is not None
    assert db.flush.await_count >= 1

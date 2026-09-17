"""验证 _require_store_update 依赖注入：FastAPI 路径参数 store_id 能正确注入 dependency。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Store, User


def _store_admin(uid=9500, store_id=9501):
    role = SimpleNamespace(code="STORE_ADMIN")
    return SimpleNamespace(id=uid, role_id=2, role=role, store_id=store_id, status=1, token_version=0)


async def _override_app(db_session: AsyncSession, user):
    from app.api.deps import get_current_user, get_db
    from app.main import app

    async def override_db():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    return app


def _clear_overrides():
    from app.main import app
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_store_admin_can_update_own_store(db_session: AsyncSession):
    """店长可以编辑自己绑定的门店。"""
    db_session.add(Store(id=9501, store_no="DEP-S1", name="依赖测试店", status=1))
    await db_session.flush()

    admin = _store_admin(store_id=9501)
    app = await _override_app(db_session, admin)
    try:
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value={"table:manage"})):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res = await ac.put("/api/v1/settings/stores/9501", json={"name": "改名后"})
    finally:
        _clear_overrides()

    assert res.status_code == 200, f"店长改本店应成功，实际 {res.status_code}: {res.text}"


@pytest.mark.asyncio
async def test_store_admin_cannot_update_other_store(db_session: AsyncSession):
    """店长不可编辑其他门店，dependency 层直接 403。"""
    db_session.add(Store(id=9501, store_no="DEP-S1", name="自己的店", status=1))
    db_session.add(Store(id=9502, store_no="DEP-S2", name="别人的店", status=1))
    await db_session.flush()

    admin = _store_admin(store_id=9501)
    app = await _override_app(db_session, admin)
    try:
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value={"table:manage"})):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res = await ac.put("/api/v1/settings/stores/9502", json={"name": "篡改"})
    finally:
        _clear_overrides()

    assert res.status_code == 403, f"店长改他店应 403，实际 {res.status_code}: {res.text}"

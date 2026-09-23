"""多门店 / 多角色隔离：规格组、公开菜单、实时频道。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.products import _assert_option_group_manage
from app.api.v1.websocket import staff_may_subscribe
from app.core.exceptions import AppException


def _user(role: str, store_id: int | None):
    return SimpleNamespace(role=SimpleNamespace(code=role), store_id=store_id)


def _group(store_id: int | None):
    return SimpleNamespace(store_id=store_id)


def test_unbound_operator_cannot_manage_option_groups():
    with pytest.raises(AppException) as exc:
        _assert_option_group_manage(_user("OPERATOR", None), _group(None))
    assert exc.value.code == 40301


def test_store_staff_cannot_edit_hq_or_other_store_groups():
    staff = _user("STORE_ADMIN", 1)
    with pytest.raises(AppException):
        _assert_option_group_manage(staff, _group(None))
    with pytest.raises(AppException):
        _assert_option_group_manage(staff, _group(2))
    _assert_option_group_manage(staff, _group(1))
    _assert_option_group_manage(_user("SUPER_ADMIN", None), _group(2))


def test_ws_subscribe_follows_permission_not_only_role():
    assert staff_may_subscribe("OPERATOR", "cameras", set()) is False
    assert staff_may_subscribe("OPERATOR", "cameras", {"camera:read"}) is True
    assert staff_may_subscribe("CASHIER", "orders", {"order:read"}) is True
    assert staff_may_subscribe("CASHIER", "dashboard", {"order:read"}) is False
    assert staff_may_subscribe("STORE_ADMIN", "dashboard", set()) is True
    assert staff_may_subscribe("KITCHEN", "kitchen", {"order:update"}) is True
    assert staff_may_subscribe("SUPER_ADMIN", "cameras", set()) is True


@pytest.mark.asyncio
async def test_public_product_list_requires_store():
    from app.api.deps import get_db, get_optional_staff_user
    from app.main import app

    fake_db = MagicMock()
    fake_db.scalar = AsyncMock(return_value=0)
    fake_db.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(__iter__=lambda self: iter([]))))
    )

    async def override_db():
        yield fake_db

    async def override_staff():
        return None

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_optional_staff_user] = override_staff
    try:
        transport = ASGITransport(app=app)
        with patch(
            "app.api.v1.products.get_redis",
            AsyncMock(side_effect=ConnectionError("redis unavailable in tests")),
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                missing = await client.get("/api/products", params={"page_size": 20})
                scoped = await client.get("/api/products", params={"page_size": 20, "store_id": 1})
        assert missing.status_code == 400
        assert missing.json()["code"] == 40001
        assert scoped.status_code == 200
        assert scoped.json()["code"] == 0
    finally:
        app.dependency_overrides.clear()

"""商品列表 page_size 封顶；财务账本只读列表。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_list_products_clamps_page_size():
    from app.api.deps import get_db, get_optional_staff_user
    from app.main import app

    fake_db = MagicMock()
    # count + list
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
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/v1/products", params={"page_size": 9999})
        assert res.status_code == 200
        body = res.json()
        assert body["code"] == 0
        assert body["data"]["page_size"] == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_financial_ledger_list_requires_auth_shape():
    from app.api.deps import get_current_user, get_db
    from app.main import app

    role = SimpleNamespace(code="STORE_ADMIN")
    user = SimpleNamespace(id=1, role_id=2, store_id=1, role=role, status=1, token_version=0)
    fake_db = MagicMock()
    count_result = MagicMock(scalar=MagicMock(return_value=0))
    list_result = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
    fake_db.execute = AsyncMock(side_effect=[count_result, list_result])

    async def override_db():
        yield fake_db

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value={"payment:read"})):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/api/v1/financial-ledger")
        assert res.status_code == 200
        body = res.json()
        assert body["code"] == 0
        assert body["data"]["items"] == []
        assert body["data"]["total"] == 0
    finally:
        app.dependency_overrides.clear()

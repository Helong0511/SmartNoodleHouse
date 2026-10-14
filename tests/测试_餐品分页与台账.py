"""餐品列表 page_size 封顶；财务账本只读列表。"""
import logging
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
            res = await client.get("/api/products", params={"page_size": 9999, "store_id": 1})
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
                res = await client.get("/api/financial-ledger")
        assert res.status_code == 200
        body = res.json()
        assert body["code"] == 0
        assert body["data"]["items"] == []
        assert body["data"]["total"] == 0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_public_product_list_rate_limit_failure_logs_warning(caplog):
    """公开菜单限流 Redis 异常：主流程 fail-open，但必须记录 warning 留痕，不能静默吞掉。"""
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

    caplog.set_level(logging.WARNING, logger="app.api.products")
    try:
        with patch(
            "app.api.products.get_redis",
            AsyncMock(side_effect=ConnectionError("redis down")),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/api/products", params={"store_id": 1})
        # 主流程不被限流检查故障阻塞
        assert res.status_code == 200
        # 限流失效必须有 warning 留痕，便于排查/防爬
        msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Public product list rate limit check failed" in m for m in msgs)
    finally:
        app.dependency_overrides.clear()

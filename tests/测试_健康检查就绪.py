"""公开健康检查：/health 与 /api/v1/ready。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_health_ok_when_db_and_redis_up():
    from app.main import app

    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=None)

    async def override_db():
        yield fake_db

    from app.core.database import get_db

    app.dependency_overrides[get_db] = override_db
    try:
        with patch("app.api.v1.health.get_redis", AsyncMock(return_value=AsyncMock(ping=AsyncMock()))):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for path in ("/health", "/ready", "/api/v1/health", "/api/v1/ready"):
                    res = await client.get(path)
                    assert res.status_code == 200, path
                    body = res.json()
                    assert body["status"] == "ok"
                    assert body["checks"]["database"] is True
                    assert body["checks"]["redis"] is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_503_when_db_down():
    from app.main import app

    fake_db = MagicMock()
    fake_db.execute = AsyncMock(side_effect=RuntimeError("db down"))

    async def override_db():
        yield fake_db

    from app.core.database import get_db

    app.dependency_overrides[get_db] = override_db
    try:
        with patch("app.api.v1.health.get_redis", AsyncMock(return_value=AsyncMock(ping=AsyncMock()))):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/health")
                assert res.status_code == 503
                body = res.json()
                assert body["status"] == "degraded"
                assert body["checks"]["database"] is False
    finally:
        app.dependency_overrides.clear()

"""公开健康检查：/health 只检查进程，/ready 检查 DB + Redis。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_health_and_readiness_ok_when_dependencies_up():
    from app.main import app

    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=None)

    async def override_db():
        yield fake_db

    from app.core.database import get_db

    app.dependency_overrides[get_db] = override_db
    try:
        with patch("app.api.health.get_redis", AsyncMock(return_value=AsyncMock(ping=AsyncMock()))):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for path in ("/health", "/api/health"):
                    res = await client.get(path)
                    assert res.status_code == 200, path
                    assert res.json() == {"status": "ok"}
                for path in ("/ready", "/api/ready"):
                    res = await client.get(path)
                    assert res.status_code == 200, path
                    body = res.json()
                    assert body["status"] == "ok"
                    assert body["checks"]["database"] is True
                    assert body["checks"]["redis"] is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_liveness_stays_healthy_when_database_is_down():
    from app.main import app

    fake_db = MagicMock()
    fake_db.execute = AsyncMock(side_effect=RuntimeError("db down"))

    async def override_db():
        yield fake_db

    from app.core.database import get_db

    app.dependency_overrides[get_db] = override_db
    try:
        with patch("app.api.health.get_redis", AsyncMock(return_value=AsyncMock(ping=AsyncMock()))):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for path in ("/health", "/api/health"):
                    res = await client.get(path)
                    assert res.status_code == 200, path
                    assert res.json() == {"status": "ok"}
                for path in ("/ready", "/api/ready"):
                    res = await client.get(path)
                    assert res.status_code == 503, path
                    body = res.json()
                    assert body["status"] == "degraded"
                    assert body["checks"]["database"] is False
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_readiness_503_when_redis_is_down():
    from app.main import app

    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=None)

    async def override_db():
        yield fake_db

    from app.core.database import get_db

    app.dependency_overrides[get_db] = override_db
    try:
        with patch("app.api.health.get_redis", AsyncMock(side_effect=RuntimeError("redis down"))):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/ready")
                assert res.status_code == 503
                body = res.json()
                assert body["status"] == "degraded"
                assert body["checks"]["database"] is True
                assert body["checks"]["redis"] is False
    finally:
        app.dependency_overrides.clear()

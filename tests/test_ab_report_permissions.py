"""Global A/B analytics must not be exposed through store-level order:read."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_store_operator_cannot_read_global_ab_experiment_report(db_session):
    from app.api.deps import get_current_user, get_db
    from app.main import app

    user = SimpleNamespace(
        id=99201,
        role_id=5,
        role=SimpleNamespace(code="OPERATOR"),
        store_id=101,
        status=1,
        token_version=0,
    )

    async def override_db():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with patch(
            "app.api.deps.get_user_permissions",
            AsyncMock(return_value={"order:read"}),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/admin/ab-experiments/noodle/report")
                assert response.status_code == 403, response.text

                performance = await client.get("/api/admin/performance/reco-metrics")
                assert performance.status_code == 403, performance.text
    finally:
        app.dependency_overrides.clear()

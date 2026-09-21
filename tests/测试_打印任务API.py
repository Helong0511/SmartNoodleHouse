"""PrintJob 列表/重试 API 冒烟（依赖覆盖，不连真实库）。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


def _staff_user(*, role_code="KITCHEN", store_id=1):
    role = SimpleNamespace(code=role_code)
    return SimpleNamespace(id=9, role_id=3, store_id=store_id, role=role, status=1, token_version=0)


@pytest.mark.asyncio
async def test_list_print_jobs_returns_items():
    from app.api.deps import get_current_user, get_db
    from app.main import app
    from app.models import PrintJob

    user = _staff_user()
    job = SimpleNamespace(
        id=1,
        store_id=1,
        order_id=10,
        ticket_type="KITCHEN",
        status="FAILED",
        attempt_count=2,
        next_retry_at=None,
        last_error="timeout",
        succeeded_at=None,
        created_at=None,
        updated_at=None,
    )

    fake_db = MagicMock()
    count_result = MagicMock(scalar=MagicMock(return_value=1))
    list_result = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[job]))))
    fake_db.execute = AsyncMock(side_effect=[count_result, list_result])
    fake_db.get = AsyncMock(return_value=None)

    async def override_db():
        yield fake_db

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value={"order:update"})):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/api/print-jobs", params={"status": "FAILED"})
        assert res.status_code == 200
        body = res.json()
        assert body["code"] == 0
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["id"] == 1
        assert body["data"]["items"][0]["status"] == "FAILED"
        # 引用模型字段，避免误删
        assert hasattr(PrintJob, "status")
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_retry_print_job_triggers_process():
    from app.api.deps import get_current_user, get_db
    from app.main import app

    user = _staff_user()
    job = SimpleNamespace(
        id=5,
        store_id=1,
        status="FAILED",
        next_retry_at=None,
        last_error="x",
    )

    fake_db = MagicMock()
    fake_db.get = AsyncMock(side_effect=[job, job])
    fake_db.commit = AsyncMock()
    fake_db.flush = AsyncMock()

    async def override_db():
        yield fake_db

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value=set())):
            with patch(
                "app.api.v1.print_jobs.PrintService.process_print_job",
                AsyncMock(return_value=True),
            ) as proc:
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    res = await client.post("/api/print-jobs/5/retry")
        assert res.status_code == 200
        body = res.json()
        assert body["code"] == 0
        assert body["data"]["ok"] is True
        proc.assert_awaited_once_with(5)
        assert job.status == "RETRYING"
    finally:
        app.dependency_overrides.clear()

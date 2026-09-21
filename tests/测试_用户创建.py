"""员工创建 API：修复 refresh 后序列化 MissingGreenlet 回归测试。"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.main import app

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="依赖真实 MySQL 种子数据；设置 RUN_INTEGRATION=1 后单独运行",
)


async def _login(client: AsyncClient) -> str:
    r = await client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("code") == 0, body
    return body["data"]["access_token"]


@pytest.mark.asyncio
async def test_create_user_returns_created_at():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client)
        headers = {"Authorization": f"Bearer {token}"}

        roles = (await client.get("/api/users/roles", headers=headers)).json()["data"]
        cashier = next(r for r in roles if r["code"] == "CASHIER")
        stores = (await client.get("/api/settings/stores/options", headers=headers)).json()["data"]["items"]
        assert stores, "测试库需至少一家门店"
        store_id = stores[0]["id"]

        username = f"testu_{int(time.time())}"
        r = await client.post(
            "/api/users",
            headers=headers,
            json={
                "username": username,
                "password": "admin123",
                "display_name": "自动化测试员",
                "role_id": cashier["id"],
                "store_id": store_id,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("code") == 0, body
        data = body["data"]
        assert data["username"] == username
        assert data["role_code"] == "CASHIER"
        assert data["store_id"] == store_id
        assert data["created_at"] is not None


if __name__ == "__main__":
    asyncio.run(test_create_user_returns_created_at())

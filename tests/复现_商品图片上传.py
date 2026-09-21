"""验证商品图片上传 API（曾出现 flush 后 MissingGreenlet 500）"""
import asyncio
import sys
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.main import app

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
BASE = "http://127.0.0.1:8000/api"


@pytest.mark.asyncio
async def test_upload_product_image_returns_200():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=30) as c:
        login = await c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        token = login.json()["data"]["access_token"]
        r = await c.post(
            "/api/products/1/image",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("t.png", PNG, "image/png")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["main_image"].startswith("/uploads/products/")


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        login = await c.post("/auth/login", json={"username": "admin", "password": "admin123"})
        if login.status_code != 200:
            print("login failed", login.text)
            return
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        products = await c.get("/products", headers=headers, params={"page": 1, "page_size": 1})
        pid = products.json()["data"]["items"][0]["id"]
        r = await c.post(
            f"/products/{pid}/image",
            headers=headers,
            files={"file": ("test.png", PNG, "image/png")},
        )
        print("upload:", r.status_code, r.text[:400])


if __name__ == "__main__":
    asyncio.run(main())

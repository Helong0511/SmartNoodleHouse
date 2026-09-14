"""应用 014 并冒烟：员工列表 + 分店库存接口。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import text
from sqlalchemy.orm import selectinload
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, engine
from app.core.security import create_access_token
from app.main import app
from app.models import User


async def apply_014() -> None:
    sql_path = os.path.join(os.path.dirname(__file__), "..", "sql", "014_multi_store_continue.sql")
    raw = open(sql_path, encoding="utf-8").read()
    stmts, buf = [], []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("--") or s.upper().startswith("USE "):
            continue
        buf.append(line)
        if s.endswith(";"):
            stmts.append("\n".join(buf))
            buf = []
    async with engine.begin() as conn:
        for st in stmts:
            await conn.execute(text(st))
    print("applied 014")


async def smoke() -> None:
    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(
                select(User).options(selectinload(User.role)).where(User.username == "admin").limit(1)
            )
        ).scalar_one_or_none()
        if not user:
            print("SKIP no admin")
            return
        token = create_access_token(user.id, {"role": user.role.code, "store_id": user.store_id})

    headers = {"Authorization": f"Bearer {token}"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/users", headers=headers)
        print("users", r.status_code, r.json().get("code"), "n=", len((r.json().get("data") or {}).get("items") or []))
        assert r.status_code == 200 and r.json().get("code") == 0

        r2 = await client.get("/api/v1/store-stocks", headers=headers, params={"store_id": 1})
        body2 = r2.json()
        print("stocks", r2.status_code, body2.get("code"), "n=", len((body2.get("data") or {}).get("items") or []))
        assert r2.status_code == 200 and body2.get("code") == 0

        r3 = await client.get("/api/v1/settings/stores/1/business", headers=headers)
        print("store-biz", r3.status_code, r3.json().get("code"))
        assert r3.status_code == 200 and r3.json().get("code") == 0


async def main() -> None:
    await apply_014()
    await smoke()


if __name__ == "__main__":
    asyncio.run(main())

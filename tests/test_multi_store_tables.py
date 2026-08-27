"""应用 sql/013 并冒烟验证桌台门店隔离。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import text
from httpx import ASGITransport, AsyncClient

from app.core.database import engine
from app.core.security import create_access_token
from app.main import app
from app.models import User
from app.core.database import AsyncSessionLocal
from sqlalchemy import select
from sqlalchemy.orm import selectinload


async def apply_sql() -> None:
    sql_path = os.path.join(os.path.dirname(__file__), "..", "sql", "013_table_store_permissions.sql")
    raw = open(sql_path, encoding="utf-8").read()
    # 去掉 USE 行，按分号拆（简单脚本）
    stmts = []
    buf = []
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
    print("applied 013 permissions")


async def smoke() -> None:
    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(
                select(User).options(selectinload(User.role)).where(User.username == "admin").limit(1)
            )
        ).scalar_one_or_none()
        if not user:
            print("SKIP: no admin")
            return
        token = create_access_token(user.id, {"role": user.role.code, "store_id": user.store_id})

    headers = {"Authorization": f"Bearer {token}"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/tables", headers=headers, params={"page_size": 5})
        body = r.json()
        print("tables", r.status_code, body.get("code"), "total=", (body.get("data") or {}).get("total"))
        assert r.status_code == 200 and body.get("code") == 0

        r2 = await client.get("/api/v1/settings/stores/options", headers=headers)
        body2 = r2.json()
        print("stores", r2.status_code, body2.get("code"), "n=", len((body2.get("data") or {}).get("items") or []))
        assert r2.status_code == 200 and body2.get("code") == 0


async def main() -> None:
    await apply_sql()
    await smoke()


if __name__ == "__main__":
    asyncio.run(main())

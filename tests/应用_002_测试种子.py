"""Apply sql/data_test.sql (idempotent) and print SH02 seed counts.

历史文件名保留；原 002_seed_test.sql 已并入 data_test.sql。
前置：已导入 sql/struct.sql + sql/data_sys.sql + sql/data_custom.sql。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import text

from app.core.database import engine

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "sql" / "data_test.sql"


async def main() -> None:
    if not SEED.is_file():
        raise SystemExit(f"missing seed file: {SEED}")

    raw = SEED.read_text(encoding="utf-8")
    parts: list[str] = []
    buf: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("--"):
            continue
        if s.upper().startswith("USE ") or s.upper().startswith("SET "):
            continue
        buf.append(line)
        if s.endswith(";"):
            parts.append("\n".join(buf))
            buf = []

    async with engine.begin() as conn:
        for stmt in parts:
            await conn.execute(text(stmt))
        row = (
            await conn.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM users WHERE username IN ('cwm2','syy2','hsf2')) AS sh02_users,
                      (SELECT COUNT(*) FROM product_categories c
                         JOIN stores s ON s.id = c.store_id WHERE s.store_no = 'SH02') AS sh02_categories,
                      (SELECT COUNT(*) FROM products p
                         JOIN stores s ON s.id = p.store_id WHERE s.store_no = 'SH02') AS sh02_products,
                      (SELECT COUNT(*) FROM tables t
                         JOIN stores s ON s.id = t.store_id WHERE s.store_no = 'SH02') AS sh02_tables,
                      (SELECT COUNT(*) FROM cameras c
                         JOIN stores s ON s.id = c.store_id WHERE s.store_no = 'SH02') AS sh02_cameras
                    """
                )
            )
        ).mappings().one()
    print("data_test applied:", dict(row))


if __name__ == "__main__":
    asyncio.run(main())

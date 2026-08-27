"""Apply sql/002_seed_test.sql (idempotent) and print seed counts."""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import text

from app.core.database import engine

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "sql" / "002_seed_test.sql"


async def main() -> None:
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
                      (SELECT COUNT(*) FROM customers WHERE customer_no LIKE 'SEED-%') AS customers,
                      (SELECT COUNT(*) FROM orders WHERE order_no LIKE 'SEED-%') AS orders,
                      (SELECT COUNT(*) FROM payments WHERE payment_no LIKE 'SEED-%') AS payments,
                      (SELECT COUNT(*) FROM payment_refunds WHERE refund_no LIKE 'SEED-%') AS refunds,
                      (SELECT COUNT(*) FROM users WHERE username = 'cashier') AS cashier
                    """
                )
            )
        ).mappings().one()
    print("seed applied:", dict(row))


if __name__ == "__main__":
    asyncio.run(main())

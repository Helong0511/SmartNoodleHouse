"""只读：打印迎宾/人脸相关 system_settings（诊断用）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text

from app.core.database import AsyncSessionLocal


async def main() -> None:
    sql = (
        "SELECT setting_key, setting_value, store_id FROM system_settings "
        "WHERE setting_key LIKE :a OR setting_key LIKE :b OR setting_key LIKE :c "
        "OR setting_key LIKE :d OR setting_key LIKE :e "
        "ORDER BY setting_key, store_id"
    )
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(sql),
                {
                    "a": "%cooldown%",
                    "b": "%welcome%",
                    "c": "%face_match%",
                    "d": "%camera_speaker%",
                    "e": "%confirm%",
                },
            )
        ).fetchall()
    for r in rows:
        print(f"{r[0]}\tstore={r[2]}\t{r[1]}")
    if not rows:
        print("(no matching system_settings rows; using config defaults)")


if __name__ == "__main__":
    asyncio.run(main())

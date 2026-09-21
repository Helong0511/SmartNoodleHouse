"""种子：会员等级（幂等，按 name 去重）。

用法:
  python scripts/seed_member_levels.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import MemberLevel

LEVELS = [
    ("普通会员", 0, Decimal("1.0000")),
    ("银卡会员", 200, Decimal("0.9800")),
    ("金卡会员", 500, Decimal("0.9500")),
    ("钻石会员", 1000, Decimal("0.9000")),
]


async def main() -> None:
    async with AsyncSessionLocal() as db:
        created = 0
        for name, min_points, discount_rate in LEVELS:
            row = (await db.execute(select(MemberLevel).where(MemberLevel.name == name))).scalar_one_or_none()
            if row:
                row.min_points = min_points
                row.discount_rate = discount_rate
                continue
            db.add(MemberLevel(name=name, min_points=min_points, discount_rate=discount_rate))
            created += 1
        await db.commit()
        total = len((await db.execute(select(MemberLevel))).scalars().all())
        print(f"member_levels: created={created}, total={total}")


if __name__ == "__main__":
    asyncio.run(main())

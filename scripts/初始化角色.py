"""种子：后台角色（幂等）。

用法（仓库根目录）:
  python scripts/seed_roles.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import Role

ROLES = [
    ("SUPER_ADMIN", "超级管理员", "跨店全部权限"),
    ("STORE_ADMIN", "门店管理员", "本店运营（含退款；不含全局系统配置）"),
    ("CASHIER", "收银员", "订单、支付与收银点餐（不可退款）"),
    ("KITCHEN", "厨房", "KDS 出餐（不可退款/不可审外卖退款）"),
    ("OPERATOR", "运营", "餐品与数据（不可退款）"),
]


async def main() -> None:
    async with AsyncSessionLocal() as db:
        created = 0
        for code, name, description in ROLES:
            existing = (await db.execute(select(Role).where(Role.code == code))).scalar_one_or_none()
            if existing:
                existing.name = name
                existing.description = description
                continue
            db.add(Role(code=code, name=name, description=description))
            created += 1
        await db.commit()
        total = (await db.execute(select(Role))).scalars().all()
        print(f"roles: created={created}, total={len(total)}")


if __name__ == "__main__":
    asyncio.run(main())

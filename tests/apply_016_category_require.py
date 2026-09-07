"""Apply 016_category_require_categories migration."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        cols = (
            await conn.execute(
                text("SHOW COLUMNS FROM product_categories LIKE 'require_category_ids'")
            )
        ).fetchall()
        if not cols:
            await conn.execute(
                text(
                    "ALTER TABLE product_categories "
                    "ADD COLUMN require_category_ids JSON DEFAULT NULL "
                    "COMMENT '须先点过的分类 id 列表（OR）；NULL/空=无限制' AFTER status"
                )
            )
            print("added require_category_ids")
        else:
            print("require_category_ids already exists")


if __name__ == "__main__":
    asyncio.run(main())

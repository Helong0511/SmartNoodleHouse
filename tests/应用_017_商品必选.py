"""Apply 017_product_require_prerequisites migration."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        for col, after in (
            ("require_category_ids", "min_order_count"),
            ("require_product_ids", "require_category_ids"),
        ):
            exists = (
                await conn.execute(
                    text(f"SHOW COLUMNS FROM products LIKE '{col}'")
                )
            ).fetchall()
            if exists:
                print(f"{col} already exists")
                continue
            comment = (
                "须先点过的分类 id（OR）；NULL=继承所属分类默认"
                if col == "require_category_ids"
                else "须先点过的指定商品 id（OR）"
            )
            await conn.execute(
                text(
                    f"ALTER TABLE products ADD COLUMN {col} JSON DEFAULT NULL "
                    f"COMMENT '{comment}' AFTER {after}"
                )
            )
            print(f"added {col}")


if __name__ == "__main__":
    asyncio.run(main())

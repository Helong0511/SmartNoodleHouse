"""Apply 015_category_store migration."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        cols = (
            await conn.execute(text("SHOW COLUMNS FROM product_categories LIKE 'store_id'"))
        ).fetchall()
        if not cols:
            await conn.execute(
                text(
                    "ALTER TABLE product_categories "
                    "ADD COLUMN store_id BIGINT UNSIGNED NULL AFTER id, "
                    "ADD INDEX idx_cat_store (store_id, sort_order, status)"
                )
            )
            print("added store_id")
        await conn.execute(
            text(
                """
                UPDATE product_categories c
                JOIN (SELECT id FROM stores ORDER BY id LIMIT 1) s
                SET c.store_id = s.id
                WHERE c.store_id IS NULL
                """
            )
        )
        nulls = (
            await conn.execute(text("SELECT COUNT(*) FROM product_categories WHERE store_id IS NULL"))
        ).scalar()
        print("nulls", nulls)
        if int(nulls or 0) == 0:
            fk = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
                        "WHERE TABLE_SCHEMA = DATABASE() "
                        "AND TABLE_NAME = 'product_categories' "
                        "AND CONSTRAINT_NAME = 'fk_cat_store'"
                    )
                )
            ).scalar()
            if not fk:
                await conn.execute(
                    text("ALTER TABLE product_categories MODIFY store_id BIGINT UNSIGNED NOT NULL")
                )
                await conn.execute(
                    text(
                        "ALTER TABLE product_categories "
                        "ADD CONSTRAINT fk_cat_store FOREIGN KEY (store_id) REFERENCES stores(id)"
                    )
                )
                print("fk added")
        rows = (
            await conn.execute(text("SELECT id, store_id, name FROM product_categories"))
        ).fetchall()
        print("rows", rows)


if __name__ == "__main__":
    asyncio.run(main())

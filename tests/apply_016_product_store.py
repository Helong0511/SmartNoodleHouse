"""Apply 016_product_store migration."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        cols = (
            await conn.execute(text("SHOW COLUMNS FROM products LIKE 'store_id'"))
        ).fetchall()
        if not cols:
            await conn.execute(
                text(
                    "ALTER TABLE products "
                    "ADD COLUMN store_id BIGINT UNSIGNED NULL AFTER id, "
                    "ADD INDEX idx_products_store (store_id, status, sort_order)"
                )
            )
            print("added store_id")

        await conn.execute(
            text(
                """
                UPDATE products p
                JOIN product_categories c ON c.id = p.category_id
                SET p.store_id = c.store_id
                WHERE p.store_id IS NULL
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE products p
                JOIN (SELECT id FROM stores ORDER BY id LIMIT 1) s
                SET p.store_id = s.id
                WHERE p.store_id IS NULL
                """
            )
        )

        # drop global unique on product_no if present
        uk = (
            await conn.execute(
                text(
                    "SELECT INDEX_NAME FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'products' "
                    "AND COLUMN_NAME = 'product_no' AND NON_UNIQUE = 0 "
                    "AND INDEX_NAME != 'PRIMARY' LIMIT 1"
                )
            )
        ).scalar()
        if uk and uk != "uk_products_store_no":
            await conn.execute(text(f"ALTER TABLE products DROP INDEX `{uk}`"))
            print("dropped", uk)

        uk2 = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'products' "
                    "AND INDEX_NAME = 'uk_products_store_no'"
                )
            )
        ).scalar()
        if not uk2:
            await conn.execute(
                text(
                    "ALTER TABLE products ADD UNIQUE KEY uk_products_store_no (store_id, product_no)"
                )
            )
            print("added uk_products_store_no")

        nulls = (
            await conn.execute(text("SELECT COUNT(*) FROM products WHERE store_id IS NULL"))
        ).scalar()
        if int(nulls or 0) == 0:
            await conn.execute(
                text("ALTER TABLE products MODIFY store_id BIGINT UNSIGNED NOT NULL")
            )
            fk = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'products' "
                        "AND CONSTRAINT_NAME = 'fk_products_store'"
                    )
                )
            ).scalar()
            if not fk:
                await conn.execute(
                    text(
                        "ALTER TABLE products "
                        "ADD CONSTRAINT fk_products_store FOREIGN KEY (store_id) REFERENCES stores(id)"
                    )
                )
                print("fk added")

        rows = (
            await conn.execute(text("SELECT id, store_id, product_no, name FROM products"))
        ).fetchall()
        print("rows", rows)


if __name__ == "__main__":
    asyncio.run(main())

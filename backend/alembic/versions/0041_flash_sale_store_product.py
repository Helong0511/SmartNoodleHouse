"""限量免费抢购：门店库存增加专属餐品列。

Revision ID: 0041_flash_sale_store_product
Revises: 0040_flash_sale_marketing

背景：products 表按门店隔离（Product.store_id 必填），总部活动无法用单一
product_id 覆盖全部门店。为 marketing_flash_sale_stocks 增加 product_id：
门店库存行可绑定本店餐品；为空时回退 FlashSaleConfig.product_id（仅单店活动）。
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0041_flash_sale_store_product"
down_revision = "0040_flash_sale_marketing"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    if conn.dialect.name == "sqlite":
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return any(r[1] == column for r in rows)
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _index_exists(conn, table: str, index: str) -> bool:
    if conn.dialect.name == "sqlite":
        rows = conn.execute(text(f"PRAGMA index_list({table})")).fetchall()
        return any(r[1] == index for r in rows)
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
            ),
            {"t": table, "i": index},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "marketing_flash_sale_stocks", "product_id"):
        if conn.dialect.name == "sqlite":
            conn.execute(
                text("ALTER TABLE marketing_flash_sale_stocks ADD COLUMN product_id INTEGER")
            )
        else:
            conn.execute(
                text(
                    "ALTER TABLE marketing_flash_sale_stocks ADD COLUMN `product_id` bigint "
                    "UNSIGNED NULL DEFAULT NULL COMMENT '本店抢购餐品；空=回退活动配置'"
                )
            )
    if not _index_exists(conn, "marketing_flash_sale_stocks", "ix_mfss_product"):
        if conn.dialect.name == "sqlite":
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_mfss_product "
                    "ON marketing_flash_sale_stocks (product_id)"
                )
            )
        else:
            conn.execute(
                text(
                    "CREATE INDEX `ix_mfss_product` ON marketing_flash_sale_stocks (`product_id`)"
                )
            )


def downgrade() -> None:
    conn = op.get_bind()
    # InnoDB may require this index to support a foreign key added by a later
    # revision. Remove any FK on product_id before dropping the supporting index
    # or column; MySQL rejects DROP INDEX while that dependency remains.
    if conn.dialect.name != "sqlite" and _column_exists(
        conn, "marketing_flash_sale_stocks", "product_id"
    ):
        foreign_keys = conn.execute(
            text(
                "SELECT DISTINCT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'marketing_flash_sale_stocks' "
                "AND COLUMN_NAME = 'product_id' "
                "AND REFERENCED_TABLE_NAME IS NOT NULL"
            )
        ).fetchall()
        for (constraint_name,) in foreign_keys:
            safe_name = str(constraint_name).replace("`", "``")
            conn.execute(
                text(
                    "ALTER TABLE marketing_flash_sale_stocks "
                    f"DROP FOREIGN KEY `{safe_name}`"
                )
            )
    if _index_exists(conn, "marketing_flash_sale_stocks", "ix_mfss_product"):
        if conn.dialect.name == "sqlite":
            conn.execute(text("DROP INDEX IF EXISTS ix_mfss_product"))
        else:
            conn.execute(text("DROP INDEX `ix_mfss_product` ON marketing_flash_sale_stocks"))
    # SQLite 不支持 DROP COLUMN（低版本）：降级保留新增列，避免破坏同库数据
    if conn.dialect.name == "sqlite":
        return
    if _column_exists(conn, "marketing_flash_sale_stocks", "product_id"):
        conn.execute(text("ALTER TABLE marketing_flash_sale_stocks DROP COLUMN `product_id`"))

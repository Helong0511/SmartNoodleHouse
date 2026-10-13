"""商品展示规则历法：公历/农历。

Revision ID: 0032_product_visibility_calendar
Revises: 0031_product_visibility
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0032_product_visibility_calendar"
down_revision = "0031_product_visibility"
branch_labels = None
depends_on = None


def _has_column(conn, table: str, column: str) -> bool:
    if conn.dialect.name == "sqlite":
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return any(r[1] == column for r in rows)
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    ).scalar()
    return row is not None


def upgrade() -> None:
    conn = op.get_bind()
    if _has_column(conn, "products", "visibility_calendar"):
        return
    if conn.dialect.name == "sqlite":
        conn.execute(
            text(
                "ALTER TABLE products ADD COLUMN visibility_calendar "
                "VARCHAR(10) NOT NULL DEFAULT 'SOLAR'"
            )
        )
        return
    conn.execute(
        text(
            "ALTER TABLE `products` ADD COLUMN `visibility_calendar` varchar(10) "
            "NOT NULL DEFAULT 'SOLAR' "
            "COMMENT '展示历法：SOLAR=公历 LUNAR=农历' "
            "AFTER `visibility_rule`"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        return
    if _has_column(conn, "products", "visibility_calendar"):
        conn.execute(text("ALTER TABLE `products` DROP COLUMN `visibility_calendar`"))

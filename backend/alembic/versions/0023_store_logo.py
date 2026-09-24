"""门店表增加 logo 字段。

Revision ID: 0023_store_logo
Revises: 0022_category_schedule_auto_sort
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0023_store_logo"
down_revision = "0022_category_schedule_auto_sort"
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
    if _has_column(conn, "stores", "logo"):
        return
    if conn.dialect.name == "sqlite":
        conn.execute(text("ALTER TABLE stores ADD COLUMN logo VARCHAR(500) NULL"))
    else:
        conn.execute(
            text(
                "ALTER TABLE `stores` "
                "ADD COLUMN `logo` varchar(500) NULL DEFAULT NULL "
                "COMMENT '门店 Logo（站内 /uploads/stores/...）' AFTER `name`"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        return
    if _has_column(conn, "stores", "logo"):
        conn.execute(text("ALTER TABLE `stores` DROP COLUMN `logo`"))

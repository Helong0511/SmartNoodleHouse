"""顾客生日确认锁定（小程序不可再改）。

Revision ID: 0033_birthday_locked
Revises: 0032_product_visibility_calendar
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0033_birthday_locked"
down_revision = "0032_product_visibility_calendar"
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
    if _has_column(conn, "customers", "birthday_locked"):
        return
    if conn.dialect.name == "sqlite":
        conn.execute(
            text(
                "ALTER TABLE customers ADD COLUMN birthday_locked "
                "INTEGER NOT NULL DEFAULT 0"
            )
        )
        return
    conn.execute(
        text(
            "ALTER TABLE `customers` ADD COLUMN `birthday_locked` "
            "tinyint NOT NULL DEFAULT 0 "
            "COMMENT '生日是否已确认锁定：1=小程序不可再改（历法/日期）；0=未锁定' "
            "AFTER `birthday_calendar`"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        return
    if _has_column(conn, "customers", "birthday_locked"):
        conn.execute(text("ALTER TABLE `customers` DROP COLUMN `birthday_locked`"))

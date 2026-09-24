"""分类时段方案：销量自动排序字段。

Revision ID: 0022_category_schedule_auto_sort
Revises: 0021_discount_scale_2
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0022_category_schedule_auto_sort"
down_revision = "0021_discount_scale_2"
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
    if not _has_column(conn, "category_sort_schedules", "auto_sort"):
        if conn.dialect.name == "sqlite":
            conn.execute(
                text(
                    "ALTER TABLE category_sort_schedules "
                    "ADD COLUMN auto_sort INTEGER NOT NULL DEFAULT 0"
                )
            )
        else:
            conn.execute(
                text(
                    "ALTER TABLE `category_sort_schedules` "
                    "ADD COLUMN `auto_sort` tinyint NOT NULL DEFAULT 0 "
                    "COMMENT '1=按该时段销量自动刷新分类排序；0=仅手动' AFTER `status`"
                )
            )
    if not _has_column(conn, "category_sort_schedules", "auto_updated_at"):
        if conn.dialect.name == "sqlite":
            conn.execute(
                text(
                    "ALTER TABLE category_sort_schedules "
                    "ADD COLUMN auto_updated_at DATETIME NULL"
                )
            )
        else:
            conn.execute(
                text(
                    "ALTER TABLE `category_sort_schedules` "
                    "ADD COLUMN `auto_updated_at` datetime(3) NULL DEFAULT NULL "
                    "COMMENT '最近一次按销量自动刷新时间' AFTER `auto_sort`"
                )
            )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        return
    if _has_column(conn, "category_sort_schedules", "auto_updated_at"):
        conn.execute(text("ALTER TABLE `category_sort_schedules` DROP COLUMN `auto_updated_at`"))
    if _has_column(conn, "category_sort_schedules", "auto_sort"):
        conn.execute(text("ALTER TABLE `category_sort_schedules` DROP COLUMN `auto_sort`"))

"""餐品展示规则：日历限时 / 生日可见。

Revision ID: 0031_product_visibility
Revises: 0030_customer_lifecycle
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0031_product_visibility"
down_revision = "0030_customer_lifecycle"
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
    sqlite = conn.dialect.name == "sqlite"
    specs = [
        (
            "visibility_rule",
            "VARCHAR(20) NOT NULL DEFAULT 'ALWAYS'",
            "`visibility_rule` varchar(20) NOT NULL DEFAULT 'ALWAYS' "
            "COMMENT '展示规则：ALWAYS/DATE_RANGE/ANNUAL_MD/BIRTHDAY_TODAY' "
            "AFTER `no_cooking`",
        ),
        (
            "visible_from",
            "DATE NULL",
            "`visible_from` date NULL DEFAULT NULL "
            "COMMENT 'DATE_RANGE起/ANNUAL_MD起始月日' "
            "AFTER `visibility_rule`",
        ),
        (
            "visible_to",
            "DATE NULL",
            "`visible_to` date NULL DEFAULT NULL "
            "COMMENT 'DATE_RANGE止/ANNUAL_MD结束月日' "
            "AFTER `visible_from`",
        ),
    ]
    for col, sqlite_ddl, mysql_ddl in specs:
        if _has_column(conn, "products", col):
            continue
        if sqlite:
            conn.execute(text(f"ALTER TABLE products ADD COLUMN {col} {sqlite_ddl}"))
        else:
            conn.execute(text(f"ALTER TABLE `products` ADD COLUMN {mysql_ddl}"))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        return
    for col in ("visible_to", "visible_from", "visibility_rule"):
        if _has_column(conn, "products", col):
            conn.execute(text(f"ALTER TABLE `products` DROP COLUMN `{col}`"))

"""优惠券模板年度发放窗口（节日券窗外不可发）。

Revision ID: 0038_coupon_tmpl_annual_md
Revises: 0037_campaign_annual_schedule
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0038_coupon_tmpl_annual_md"
down_revision = "0037_campaign_annual_schedule"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
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
    return bool(row)


def _index_exists(conn, table: str, index: str) -> bool:
    if conn.dialect.name == "sqlite":
        rows = conn.execute(text(f"PRAGMA index_list({table})")).fetchall()
        return any(r[1] == index for r in rows)
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
        ),
        {"t": table, "i": index},
    ).scalar()
    return bool(row)


def upgrade() -> None:
    conn = op.get_bind()
    cols = [
        (
            "schedule_mode",
            "ALTER TABLE coupon_templates ADD COLUMN schedule_mode "
            "VARCHAR(20) NOT NULL DEFAULT 'ALWAYS' "
            "COMMENT 'ALWAYS=全年可发；ANNUAL_MD=仅每年月日窗口内可发'",
            "ALTER TABLE coupon_templates ADD COLUMN schedule_mode "
            "VARCHAR(20) NOT NULL DEFAULT 'ALWAYS'",
        ),
        (
            "schedule_calendar",
            "ALTER TABLE coupon_templates ADD COLUMN schedule_calendar "
            "VARCHAR(10) NOT NULL DEFAULT 'SOLAR' "
            "COMMENT 'SOLAR=公历 LUNAR=农历（仅 ANNUAL_MD）'",
            "ALTER TABLE coupon_templates ADD COLUMN schedule_calendar "
            "VARCHAR(10) NOT NULL DEFAULT 'SOLAR'",
        ),
        (
            "annual_start_month",
            "ALTER TABLE coupon_templates ADD COLUMN annual_start_month "
            "TINYINT UNSIGNED NULL DEFAULT NULL COMMENT '发放窗起月 1-12'",
            "ALTER TABLE coupon_templates ADD COLUMN annual_start_month INTEGER",
        ),
        (
            "annual_start_day",
            "ALTER TABLE coupon_templates ADD COLUMN annual_start_day "
            "TINYINT UNSIGNED NULL DEFAULT NULL COMMENT '发放窗起日 1-31'",
            "ALTER TABLE coupon_templates ADD COLUMN annual_start_day INTEGER",
        ),
        (
            "annual_end_month",
            "ALTER TABLE coupon_templates ADD COLUMN annual_end_month "
            "TINYINT UNSIGNED NULL DEFAULT NULL COMMENT '发放窗止月'",
            "ALTER TABLE coupon_templates ADD COLUMN annual_end_month INTEGER",
        ),
        (
            "annual_end_day",
            "ALTER TABLE coupon_templates ADD COLUMN annual_end_day "
            "TINYINT UNSIGNED NULL DEFAULT NULL COMMENT '发放窗止日'",
            "ALTER TABLE coupon_templates ADD COLUMN annual_end_day INTEGER",
        ),
    ]
    for name, mysql_sql, sqlite_sql in cols:
        if _column_exists(conn, "coupon_templates", name):
            continue
        conn.execute(text(sqlite_sql if conn.dialect.name == "sqlite" else mysql_sql))
    if not _index_exists(conn, "coupon_templates", "idx_coupon_tmpl_schedule"):
        if conn.dialect.name == "sqlite":
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_coupon_tmpl_schedule "
                    "ON coupon_templates (schedule_mode, status)"
                )
            )
        else:
            conn.execute(
                text(
                    "CREATE INDEX idx_coupon_tmpl_schedule "
                    "ON coupon_templates (schedule_mode, status)"
                )
            )


def downgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, "coupon_templates", "idx_coupon_tmpl_schedule"):
        if conn.dialect.name == "sqlite":
            conn.execute(text("DROP INDEX IF EXISTS idx_coupon_tmpl_schedule"))
        else:
            conn.execute(text("ALTER TABLE coupon_templates DROP INDEX idx_coupon_tmpl_schedule"))
    for col in (
        "annual_end_day",
        "annual_end_month",
        "annual_start_day",
        "annual_start_month",
        "schedule_calendar",
        "schedule_mode",
    ):
        if not _column_exists(conn, "coupon_templates", col):
            continue
        if conn.dialect.name == "sqlite":
            continue
        conn.execute(text(f"ALTER TABLE coupon_templates DROP COLUMN {col}"))

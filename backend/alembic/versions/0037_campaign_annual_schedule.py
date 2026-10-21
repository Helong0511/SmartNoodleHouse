"""营销活动年度公历/农历排期字段。

Revision ID: 0037_campaign_annual_schedule
Revises: 0036_audit_remaining_features
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0037_campaign_annual_schedule"
down_revision = "0036_audit_remaining_features"
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
            "ALTER TABLE marketing_campaigns ADD COLUMN schedule_mode "
            "VARCHAR(20) NOT NULL DEFAULT 'ABSOLUTE' "
            "COMMENT 'ABSOLUTE=绝对起止；ANNUAL_MD=每年月日自动启停'",
            "ALTER TABLE marketing_campaigns ADD COLUMN schedule_mode "
            "VARCHAR(20) NOT NULL DEFAULT 'ABSOLUTE'",
        ),
        (
            "schedule_calendar",
            "ALTER TABLE marketing_campaigns ADD COLUMN schedule_calendar "
            "VARCHAR(10) NOT NULL DEFAULT 'SOLAR' "
            "COMMENT 'SOLAR=公历 LUNAR=农历（仅 ANNUAL_MD）'",
            "ALTER TABLE marketing_campaigns ADD COLUMN schedule_calendar "
            "VARCHAR(10) NOT NULL DEFAULT 'SOLAR'",
        ),
        (
            "annual_start_month",
            "ALTER TABLE marketing_campaigns ADD COLUMN annual_start_month "
            "TINYINT UNSIGNED NULL DEFAULT NULL COMMENT '年度窗起月 1-12'",
            "ALTER TABLE marketing_campaigns ADD COLUMN annual_start_month INTEGER",
        ),
        (
            "annual_start_day",
            "ALTER TABLE marketing_campaigns ADD COLUMN annual_start_day "
            "TINYINT UNSIGNED NULL DEFAULT NULL COMMENT '年度窗起日 1-31'",
            "ALTER TABLE marketing_campaigns ADD COLUMN annual_start_day INTEGER",
        ),
        (
            "annual_end_month",
            "ALTER TABLE marketing_campaigns ADD COLUMN annual_end_month "
            "TINYINT UNSIGNED NULL DEFAULT NULL COMMENT '年度窗止月'",
            "ALTER TABLE marketing_campaigns ADD COLUMN annual_end_month INTEGER",
        ),
        (
            "annual_end_day",
            "ALTER TABLE marketing_campaigns ADD COLUMN annual_end_day "
            "TINYINT UNSIGNED NULL DEFAULT NULL COMMENT '年度窗止日'",
            "ALTER TABLE marketing_campaigns ADD COLUMN annual_end_day INTEGER",
        ),
    ]
    for name, mysql_ddl, sqlite_ddl in cols:
        if _column_exists(conn, "marketing_campaigns", name):
            continue
        conn.execute(text(sqlite_ddl if conn.dialect.name == "sqlite" else mysql_ddl))
    if not _index_exists(conn, "marketing_campaigns", "idx_mc_schedule_mode"):
        if conn.dialect.name == "sqlite":
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_mc_schedule_mode "
                    "ON marketing_campaigns (schedule_mode, status)"
                )
            )
        else:
            conn.execute(
                text(
                    "ALTER TABLE marketing_campaigns "
                    "ADD INDEX idx_mc_schedule_mode (schedule_mode ASC, status ASC)"
                )
            )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        return
    if _index_exists(conn, "marketing_campaigns", "idx_mc_schedule_mode"):
        conn.execute(text("ALTER TABLE marketing_campaigns DROP INDEX idx_mc_schedule_mode"))
    for col in (
        "annual_end_day",
        "annual_end_month",
        "annual_start_day",
        "annual_start_month",
        "schedule_calendar",
        "schedule_mode",
    ):
        if _column_exists(conn, "marketing_campaigns", col):
            conn.execute(text(f"ALTER TABLE marketing_campaigns DROP COLUMN {col}"))

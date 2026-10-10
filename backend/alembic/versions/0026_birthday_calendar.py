"""顾客生日历法 + 生日积分业务参数种子。

Revision ID: 0026_birthday_calendar
Revises: 0025_daily_ux_features
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0026_birthday_calendar"
down_revision = "0025_daily_ux_features"
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
    if not _has_column(conn, "customers", "birthday_calendar"):
        if sqlite:
            conn.execute(
                text(
                    "ALTER TABLE customers ADD COLUMN birthday_calendar "
                    "VARCHAR(10) NOT NULL DEFAULT 'SOLAR'"
                )
            )
        else:
            conn.execute(
                text(
                    "ALTER TABLE `customers` ADD COLUMN `birthday_calendar` "
                    "varchar(10) NOT NULL DEFAULT 'SOLAR' "
                    "COMMENT '生日历法：SOLAR=公历 LUNAR=农历（按农历月日匹配权益日）' "
                    "AFTER `birthday`"
                )
            )
    # 系统参数种子（幂等）
    for key, value, desc in (
        ("birthday_benefit_mode", "points", "生日优惠方式：off/points/coupon/points_and_coupon"),
        ("birthday_points_multiplier", "2", "生日积分倍数（相对实付1元=1分）"),
        ("birthday_coupon_name", "", "生日赠券模板名（空=不发）"),
    ):
        exists = conn.execute(
            text("SELECT 1 FROM system_settings WHERE setting_key = :k LIMIT 1"),
            {"k": key},
        ).scalar()
        if exists:
            continue
        conn.execute(
            text(
                "INSERT INTO system_settings (setting_key, setting_value, description) "
                "VALUES (:k, :v, :d)"
            ),
            {"k": key, "v": value, "d": desc},
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        return
    if _has_column(conn, "customers", "birthday_calendar"):
        conn.execute(text("ALTER TABLE `customers` DROP COLUMN `birthday_calendar`"))

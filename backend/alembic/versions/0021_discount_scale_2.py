"""折扣字段统一为两位小数。

member_levels.discount_rate：decimal(5,2)
coupon_templates.discount_value：decimal(10,2)（满减/代金为金额，折扣券为 0.90 这类比例）

Revision ID: 0021_discount_scale_2
Revises: 0020_store_scope_not_null
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0021_discount_scale_2"
down_revision = "0020_store_scope_not_null"
branch_labels = None
depends_on = None


def _numeric_scale(conn, table: str, column: str) -> int | None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return None
    row = conn.execute(
        text(
            "SELECT NUMERIC_SCALE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    ).scalar()
    return int(row) if row is not None else None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        return
    if _numeric_scale(conn, "member_levels", "discount_rate") not in (None, 2):
        conn.execute(
            text(
                "ALTER TABLE member_levels "
                "MODIFY COLUMN discount_rate DECIMAL(5, 2) NOT NULL DEFAULT 1.00 "
                "COMMENT '折扣率，保留两位：1.00=无折扣，0.90=九折，0.98=9.8折'"
            )
        )
    if _numeric_scale(conn, "coupon_templates", "discount_value") not in (None, 2):
        conn.execute(
            text(
                "ALTER TABLE coupon_templates "
                "MODIFY COLUMN discount_value DECIMAL(10, 2) NOT NULL "
                "COMMENT 'FIXED/VOUCHER=减免金额（元，两位）；PERCENT=折扣率两位，如0.90表示九折'"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        return
    if _numeric_scale(conn, "coupon_templates", "discount_value") == 2:
        conn.execute(
            text(
                "ALTER TABLE coupon_templates "
                "MODIFY COLUMN discount_value DECIMAL(10, 4) NOT NULL "
                "COMMENT 'FIXED/VOUCHER=减免金额；PERCENT=折扣比例如0.90表示九折'"
            )
        )

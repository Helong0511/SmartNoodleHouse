"""优惠券总部模板门店白名单 store_whitelist

Revision ID: 0014_coupon_template_whitelist
Revises: 0013_identity_phone_security
Create Date: 2026-09-14
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0014_coupon_template_whitelist"
down_revision = "0013_identity_phone_security"
branch_labels = None
depends_on = None


def _col_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = :t AND column_name = :c
            """
        ),
        {"t": table, "c": column},
    ).scalar()
    return int(row or 0) > 0


def upgrade() -> None:
    conn = op.get_bind()
    # P2-4: NULL=全部门店可见；JSON 数组=仅列出门店可见
    if not _col_exists(conn, "coupon_templates", "store_whitelist"):
        conn.execute(
            text(
                "ALTER TABLE coupon_templates "
                "ADD COLUMN store_whitelist JSON DEFAULT NULL "
                "COMMENT '总部模板可见门店白名单；NULL=全部门店' AFTER remaining"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _col_exists(conn, "coupon_templates", "store_whitelist"):
        conn.execute(text("ALTER TABLE coupon_templates DROP COLUMN store_whitelist"))

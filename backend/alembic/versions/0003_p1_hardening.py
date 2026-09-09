"""P1 hardening: welcome coupon UNIQUE(customer_id, template_id)

Revision ID: 0003_p1_hardening
Revises: 0002_p0_hardening
Create Date: 2026-09-09
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0003_p1_hardening"
down_revision = "0002_p0_hardening"
branch_labels = None
depends_on = None


def _scalar(conn, sql: str, params: dict | None = None) -> int:
    row = conn.execute(text(sql), params or {}).scalar()
    return int(row or 0)


def _table_exists(conn, table: str) -> bool:
    return (
        _scalar(
            conn,
            """
            SELECT COUNT(*) FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t
            """,
            {"t": table},
        )
        > 0
    )


def _index_exists(conn, table: str, index: str) -> bool:
    return (
        _scalar(
            conn,
            """
            SELECT COUNT(*) FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i
            """,
            {"t": table, "i": index},
        )
        > 0
    )


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "customer_coupons"):
        return
    if _index_exists(conn, "customer_coupons", "uk_cc_customer_template"):
        return
    # 先清重复（保留最小 id），再加唯一约束，避免欢迎券并发重复领取
    conn.execute(
        text(
            """
            DELETE cc FROM customer_coupons cc
            INNER JOIN customer_coupons keep
              ON keep.customer_id = cc.customer_id
             AND keep.template_id = cc.template_id
             AND keep.id < cc.id
            """
        )
    )
    conn.execute(
        text(
            """
            ALTER TABLE customer_coupons
              ADD UNIQUE INDEX uk_cc_customer_template (customer_id, template_id)
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "customer_coupons") and _index_exists(
        conn, "customer_coupons", "uk_cc_customer_template"
    ):
        conn.execute(text("ALTER TABLE customer_coupons DROP INDEX uk_cc_customer_template"))

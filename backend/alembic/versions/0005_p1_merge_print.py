"""P1: customer coupon status_reason + financial_ledger notes (no new tables)

Revision ID: 0005_p1_merge_print
Revises: 0004_p1_shift_open
Create Date: 2026-09-09

Customer merge 复用 operation_logs，不另建 merge_audit。
FinancialLedger 表已在 baseline 存在。
本迁移仅为 customer_coupons 增加 status_reason（合并重复券标记 merge_dup）。
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0005_p1_merge_print"
down_revision = "0004_p1_shift_open"
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


def _column_exists(conn, table: str, column: str) -> bool:
    return (
        _scalar(
            conn,
            """
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c
            """,
            {"t": table, "c": column},
        )
        > 0
    )


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "customer_coupons"):
        return
    if not _column_exists(conn, "customer_coupons", "status_reason"):
        conn.execute(
            text(
                """
                ALTER TABLE customer_coupons
                  ADD COLUMN status_reason VARCHAR(40) DEFAULT NULL
                    COMMENT '状态原因：merge_dup=合并冲突作废 等'
                """
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "customer_coupons"):
        return
    if _column_exists(conn, "customer_coupons", "status_reason"):
        conn.execute(text("ALTER TABLE customer_coupons DROP COLUMN status_reason"))

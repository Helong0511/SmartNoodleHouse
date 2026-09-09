"""生产索引与连接池相关硬化

Revision ID: 0007_prod_indexes
Revises: 0006_print_jobs
Create Date: 2026-09-09
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0007_prod_indexes"
down_revision = "0006_print_jobs"
branch_labels = None
depends_on = None


def _index_exists(conn, table: str, index: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = :t AND index_name = :i
            """
        ),
        {"t": table, "i": index},
    ).scalar()
    return int(row or 0) > 0


def upgrade() -> None:
    conn = op.get_bind()
    indexes = [
        ("orders", "idx_orders_store_pay_paid", "(store_id, payment_status, paid_at)"),
        ("payments", "idx_payments_status_paid", "(status, paid_at)"),
        ("payment_refunds", "idx_refunds_status_succ", "(status, succeeded_at)"),
        (
            "financial_ledger",
            "idx_fl_store_ch_type_created",
            "(store_id, channel, entry_type, created_at)",
        ),
        (
            "customer_coupons",
            "idx_cc_customer_status_exp",
            "(customer_id, status, expires_at)",
        ),
    ]
    for table, name, cols in indexes:
        if _index_exists(conn, table, name):
            continue
        conn.execute(text(f"ALTER TABLE {table} ADD INDEX {name} {cols}"))


def downgrade() -> None:
    conn = op.get_bind()
    for table, name, _ in [
        ("orders", "idx_orders_store_pay_paid", ""),
        ("payments", "idx_payments_status_paid", ""),
        ("payment_refunds", "idx_refunds_status_succ", ""),
        ("financial_ledger", "idx_fl_store_ch_type_created", ""),
        ("customer_coupons", "idx_cc_customer_status_exp", ""),
    ]:
        if _index_exists(conn, table, name):
            conn.execute(text(f"ALTER TABLE {table} DROP INDEX {name}"))

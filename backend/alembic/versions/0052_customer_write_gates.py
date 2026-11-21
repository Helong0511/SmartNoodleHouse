"""Create durable per-customer write gates and backfill existing accounts.

Revision ID: 0052_customer_write_gates
Revises: 0051_pos_refund_fencing
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT as MySQLBigInteger
from alembic import op

revision = "0052_customer_write_gates"
down_revision = "0051_pos_refund_fencing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("customer_write_gates"):
        op.create_table(
            "customer_write_gates",
            sa.Column(
                "customer_id",
                MySQLBigInteger(unsigned=True).with_variant(sa.Integer(), "sqlite"),
                sa.ForeignKey("customers.id", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            comment="One transaction mutex row per customer; no business state",
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
            mysql_collate="utf8mb4_unicode_ci",
        )

    # Record why an outstanding flash-sale entitlement became terminal.
    if inspector.has_table("marketing_flash_sale_claims") and "status_reason" not in {
        c["name"] for c in inspector.get_columns("marketing_flash_sale_claims")
    }:
        op.add_column(
            "marketing_flash_sale_claims",
            sa.Column("status_reason", sa.String(length=40), nullable=True),
        )

    # Ensure all future inserts, including SQLAlchemy Core/bulk inserts that
    # bypass ORM mapper events, receive a gate in the same database transaction.
    if conn.dialect.name == "mysql":
        op.execute(
            "CREATE TRIGGER trg_customers_create_write_gate "
            "AFTER INSERT ON customers FOR EACH ROW "
            "INSERT IGNORE INTO customer_write_gates (customer_id) VALUES (NEW.id)"
        )

    # Idempotent backfill: supports partially provisioned environments and can
    # be safely re-run after a failed deployment.
    if inspector.has_table("customers"):
        op.execute(
            sa.text(
                "INSERT INTO customer_write_gates (customer_id) "
                "SELECT c.id FROM customers AS c "
                "LEFT JOIN customer_write_gates AS g ON g.customer_id = c.id "
                "WHERE g.customer_id IS NULL"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "mysql":
        op.execute("DROP TRIGGER IF EXISTS trg_customers_create_write_gate")
    inspector = sa.inspect(conn)
    if inspector.has_table("marketing_flash_sale_claims") and "status_reason" in {
        c["name"] for c in inspector.get_columns("marketing_flash_sale_claims")
    }:
        op.drop_column("marketing_flash_sale_claims", "status_reason")
    if inspector.has_table("customer_write_gates"):
        op.drop_table("customer_write_gates")

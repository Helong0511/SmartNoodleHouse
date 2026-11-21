"""Create durable per-customer write gates and backfill existing accounts.

Revision ID: 0052_customer_write_gates
Revises: 0051_pos_refund_fencing
"""
from __future__ import annotations

import sqlalchemy as sa
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
                sa.BigInteger(unsigned=True),
                sa.ForeignKey("customers.id", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            comment="One transaction mutex row per customer; no business state",
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
    if sa.inspect(conn).has_table("customer_write_gates"):
        op.drop_table("customer_write_gates")

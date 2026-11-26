"""Bind POS payment records to their originating cashier shift.

Revision ID: 0052_pos_payment_shift
Revises: 0051_pos_refund_fencing
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0052_pos_payment_shift"
down_revision = "0051_pos_refund_fencing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("payments"):
        return

    columns = {item["name"] for item in inspector.get_columns("payments")}
    if "cashier_shift_id" not in columns:
        op.add_column(
            "payments",
            sa.Column("cashier_shift_id", sa.BigInteger(), nullable=True),
        )

    # Keep the FK optional for legacy installations where POS shift tables were
    # not installed, but enforce referential integrity where the table exists.
    if inspector.has_table("cashier_shifts"):
        fk_names = {
            item.get("name")
            for item in inspector.get_foreign_keys("payments")
        }
        if "fk_payments_cashier_shift_id" not in fk_names:
            op.create_foreign_key(
                "fk_payments_cashier_shift_id",
                "payments",
                "cashier_shifts",
                ["cashier_shift_id"],
                ["id"],
                ondelete="SET NULL",
            )

    index_names = {item["name"] for item in inspector.get_indexes("payments")}
    if "ix_payments_cashier_shift_status" not in index_names:
        op.create_index(
            "ix_payments_cashier_shift_status",
            "payments",
            ["cashier_shift_id", "status"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("payments"):
        return

    index_names = {item["name"] for item in inspector.get_indexes("payments")}
    if "ix_payments_cashier_shift_status" in index_names:
        op.drop_index("ix_payments_cashier_shift_status", table_name="payments")

    fk_names = {
        item.get("name")
        for item in inspector.get_foreign_keys("payments")
    }
    if "fk_payments_cashier_shift_id" in fk_names:
        op.drop_constraint(
            "fk_payments_cashier_shift_id", "payments", type_="foreignkey"
        )

    columns = {item["name"] for item in inspector.get_columns("payments")}
    if "cashier_shift_id" in columns:
        op.drop_column("payments", "cashier_shift_id")

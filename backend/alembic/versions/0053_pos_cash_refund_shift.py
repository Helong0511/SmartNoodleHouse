"""Attribute POS cash refunds to the cashier shift that executed them.

Revision ID: 0053_pos_cash_refund_shift
Revises: 0052_pos_payment_shift
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from alembic import op

revision = "0053_pos_cash_refund_shift"
down_revision = "0052_pos_payment_shift"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("payment_refunds"):
        return

    columns = {item["name"] for item in inspector.get_columns("payment_refunds")}
    has_shift_table = inspector.has_table("cashier_shifts")
    fk_names = {item.get("name") for item in inspector.get_foreign_keys("payment_refunds")}
    needs_column = "cashier_shift_id" not in columns
    needs_fk = has_shift_table and "fk_payment_refunds_cashier_shift_id" not in fk_names

    if conn.dialect.name == "sqlite":
        if needs_column or needs_fk:
            with op.batch_alter_table("payment_refunds", recreate="always") as batch:
                if needs_column:
                    batch.add_column(sa.Column(
                        "cashier_shift_id",
                        sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql"),
                        nullable=True,
                    ))
                if needs_fk:
                    batch.create_foreign_key(
                        "fk_payment_refunds_cashier_shift_id",
                        "cashier_shifts",
                        ["cashier_shift_id"],
                        ["id"],
                        ondelete="SET NULL",
                    )
    else:
        if needs_column:
            op.add_column(
                "payment_refunds",
                sa.Column(
                    "cashier_shift_id",
                    sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql"),
                    nullable=True,
                ),
            )
        if needs_fk:
            op.create_foreign_key(
                "fk_payment_refunds_cashier_shift_id",
                "payment_refunds",
                "cashier_shifts",
                ["cashier_shift_id"],
                ["id"],
                ondelete="SET NULL",
            )

    indexes = {item["name"] for item in sa.inspect(conn).get_indexes("payment_refunds")}
    if "ix_pr_cashier_shift_status" not in indexes:
        op.create_index(
            "ix_pr_cashier_shift_status",
            "payment_refunds",
            ["cashier_shift_id", "status"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("payment_refunds"):
        return

    fk_names = {item.get("name") for item in inspector.get_foreign_keys("payment_refunds")}
    has_fk = "fk_payment_refunds_cashier_shift_id" in fk_names
    columns = {item["name"] for item in inspector.get_columns("payment_refunds")}
    has_column = "cashier_shift_id" in columns
    indexes = {item["name"] for item in inspector.get_indexes("payment_refunds")}
    has_index = "ix_pr_cashier_shift_status" in indexes

    if conn.dialect.name == "sqlite":
        if has_fk or has_column or has_index:
            with op.batch_alter_table("payment_refunds", recreate="always") as batch:
                if has_fk:
                    batch.drop_constraint("fk_payment_refunds_cashier_shift_id", type_="foreignkey")
                if has_index:
                    batch.drop_index("ix_pr_cashier_shift_status")
                if has_column:
                    batch.drop_column("cashier_shift_id")
    else:
        if has_fk:
            op.drop_constraint(
                "fk_payment_refunds_cashier_shift_id",
                "payment_refunds",
                type_="foreignkey",
            )
        if has_index:
            op.drop_index("ix_pr_cashier_shift_status", table_name="payment_refunds")
        if has_column:
            op.drop_column("payment_refunds", "cashier_shift_id")

"""Preserve original customer identity on point and wallet ledger rows.

Revision ID: 0052_customer_ledger_origin
Revises: 0051_pos_refund_fencing
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0052_customer_ledger_origin"
down_revision = "0051_pos_refund_fencing"
branch_labels = None
depends_on = None


def _add_origin_column(table: str) -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table(table):
        return
    columns = {item["name"] for item in inspector.get_columns(table)}
    if "origin_customer_id" not in columns:
        op.add_column(
            table,
            sa.Column(
                "origin_customer_id",
                sa.BigInteger(),
                sa.ForeignKey("customers.id", ondelete="SET NULL"),
                nullable=True,
                comment="Original customer account that generated the ledger snapshot",
            ),
        )
    indexes = {item["name"] for item in sa.inspect(conn).get_indexes(table)}
    index_name = f"ix_{table}_origin_customer_id"
    if index_name not in indexes:
        op.create_index(index_name, table, ["origin_customer_id"])
    # Existing ledger rows predate this distinction. Their current owner is
    # the only reliable origin available before any subsequent account merge.
    conn.execute(
        sa.text(
            f"UPDATE {table} SET origin_customer_id = customer_id "
            "WHERE origin_customer_id IS NULL"
        )
    )


def upgrade() -> None:
    _add_origin_column("point_ledger")
    _add_origin_column("balance_ledger")


def downgrade() -> None:
    conn = op.get_bind()
    for table in ("balance_ledger", "point_ledger"):
        inspector = sa.inspect(conn)
        if not inspector.has_table(table):
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        index_name = f"ix_{table}_origin_customer_id"
        indexes = {item["name"] for item in inspector.get_indexes(table)}
        if index_name in indexes:
            op.drop_index(index_name, table_name=table)
        if "origin_customer_id" in columns:
            op.drop_column(table, "origin_customer_id")

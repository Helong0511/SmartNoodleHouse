"""Index Outbox aggregate lookups used for durable recommendation cache epochs.

Revision ID: 0053_outbox_aggregate_lookup
Revises: 0052_customer_write_gates
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053_outbox_aggregate_lookup"
down_revision = "0052_customer_write_gates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_table("outbox_events") and not inspector.has_index(
        "outbox_events", "idx_oe_aggregate_latest"
    ):
        op.create_index(
            "idx_oe_aggregate_latest",
            "outbox_events",
            ["aggregate_type", "aggregate_id", "id"],
            unique=False,
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_table("outbox_events") and inspector.has_index(
        "outbox_events", "idx_oe_aggregate_latest"
    ):
        op.drop_index("idx_oe_aggregate_latest", table_name="outbox_events")

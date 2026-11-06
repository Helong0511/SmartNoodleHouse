"""Add explicit store scope to payment reconciliation batches.

Revision ID: 0048_reconcile_store_scope
Revises: 0047_payment_refund_idempotency_key

Existing batches are retained as HQ/global scope (scope_store_id=0). New store-level
imports receive a positive store ID and are isolated by a composite unique key.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048_reconcile_store_scope"
down_revision = "0047_payment_refund_idempotency_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode supports SQLite migration tests and MySQL production databases.
    with op.batch_alter_table("payment_reconcile_batches") as batch:
        batch.add_column(
            sa.Column(
                "scope_store_id",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
                comment="0=HQ/global reconciliation; positive value=store ID",
            )
        )
        batch.drop_constraint("uk_prb_channel_bill", type_="unique")
        batch.create_unique_constraint(
            "uk_prb_channel_bill_store",
            ["channel", "bill_date", "scope_store_id"],
        )
        batch.create_index(
            "ix_payment_reconcile_batches_scope_store_id",
            ["scope_store_id"],
            unique=False,
        )


def downgrade() -> None:
    # A downgrade would collapse multiple store-specific batches for the same
    # channel/date into one row and could destroy reconciliation evidence.
    # Deliberately refuse destructive rollback; restore from a reviewed backup
    # and a purpose-built consolidation migration if rollback is ever required.
    raise RuntimeError(
        "0048_reconcile_store_scope is intentionally irreversible: "
        "store-scoped batches cannot be safely collapsed into a global batch"
    )

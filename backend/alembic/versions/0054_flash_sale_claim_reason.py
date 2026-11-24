"""Add the durable cancellation reason to flash-sale claims.

Revision ID: 0054_flash_sale_claim_reason
Revises: 0053_outbox_aggregate_lookup
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0054_flash_sale_claim_reason"
down_revision = "0053_outbox_aggregate_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("marketing_flash_sale_claims"):
        raise RuntimeError(
            "marketing_flash_sale_claims is missing; apply migration 0040 before 0054"
        )
    if not inspector.has_column("marketing_flash_sale_claims", "status_reason"):
        op.add_column(
            "marketing_flash_sale_claims",
            sa.Column("status_reason", sa.String(length=40), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_table("marketing_flash_sale_claims") and inspector.has_column(
        "marketing_flash_sale_claims", "status_reason"
    ):
        op.drop_column("marketing_flash_sale_claims", "status_reason")

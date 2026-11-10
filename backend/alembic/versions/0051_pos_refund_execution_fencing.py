"""Add durable execution fencing to POS refund approvals.

Revision ID: 0051_pos_refund_fencing
Revises: 0050_menu_db_generation
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051_pos_refund_fencing"
down_revision = "0050_menu_db_generation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    columns = {item["name"] for item in sa.inspect(conn).get_columns("pos_refund_requests")}
    if "execution_epoch" not in columns:
        op.add_column(
            "pos_refund_requests",
            sa.Column(
                "execution_epoch",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
                comment="Monotonic POS refund execution fencing epoch",
            ),
        )
    if "execution_token" not in columns:
        op.add_column(
            "pos_refund_requests",
            sa.Column(
                "execution_token",
                sa.String(length=64),
                nullable=True,
                comment="Random execution owner token; never used as a credential",
            ),
        )
    if "execution_lease_until" not in columns:
        op.add_column(
            "pos_refund_requests",
            sa.Column(
                "execution_lease_until",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="Lease deadline before the first refund row is durably bound",
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    columns = {item["name"] for item in sa.inspect(conn).get_columns("pos_refund_requests")}
    if "execution_lease_until" in columns:
        op.drop_column("pos_refund_requests", "execution_lease_until")
    if "execution_token" in columns:
        op.drop_column("pos_refund_requests", "execution_token")
    if "execution_epoch" in columns:
        op.drop_column("pos_refund_requests", "execution_epoch")

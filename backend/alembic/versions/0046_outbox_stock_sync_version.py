"""外卖库存镜像 Outbox 增加版本号，合并处理期间的新库存变化。

Revision ID: 0046_outbox_stock_sync_version
Revises: 0045_leader_fencing_epoch
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0046_outbox_stock_sync_version"
down_revision = "0045_leader_fencing_epoch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("outbox_events"):
        raise RuntimeError(
            "Cannot apply 0046_outbox_stock_sync_version: required table "
            "'outbox_events' is missing. Restore the canonical baseline before retrying."
        )
    columns = {column["name"] for column in inspector.get_columns("outbox_events")}
    if "sync_version" not in columns:
        op.add_column(
            "outbox_events",
            sa.Column(
                "sync_version",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    # MySQL commits DDL independently. Earlier revisions can be partially
    # applied, so the table itself may not exist when this downgrade runs.
    if not inspector.has_table("outbox_events"):
        return
    columns = {column["name"] for column in inspector.get_columns("outbox_events")}
    if "sync_version" in columns:
        op.drop_column("outbox_events", "sync_version")

"""持久化 Leader fencing epoch，阻止旧 Leader 数据库事务在新 Leader 接管后提交。

Revision ID: 0045_leader_fencing_epoch
Revises: 0044_worker_lease_timestamp_precision
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_leader_fencing_epoch"
down_revision = "0044_worker_lease_ts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # Baseline schema may already contain this table on a fresh installation.
    # Keep the revision safe for both baseline-created and legacy databases.
    if not sa.inspect(conn).has_table("leader_fencing_state"):
        op.create_table(
            "leader_fencing_state",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("epoch", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("owner_id", sa.String(length=64), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
            mysql_collate="utf8mb4_unicode_ci",
        )
    conn.execute(
        sa.text(
            "INSERT INTO leader_fencing_state (id, epoch, owner_id) "
            "SELECT 1, 0, NULL WHERE NOT EXISTS "
            "(SELECT 1 FROM leader_fencing_state WHERE id = 1)"
        )
    )


def downgrade() -> None:
    op.drop_table("leader_fencing_state")

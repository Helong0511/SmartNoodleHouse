"""Persist menu cache generation and active schedule in MySQL.

Revision ID: 0050_menu_db_generation
Revises: 0049_menu_cache_outbox_retry
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050_menu_db_generation"
down_revision = "0049_menu_cache_outbox_retry"
branch_labels = None
depends_on = None
GENERATION_BASELINE = 1_000_000_000


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    # Stores is a required application table. A successful Alembic revision
    # must not conceal an incomplete canonical baseline.
    if not inspector.has_table("stores"):
        raise RuntimeError(
            "Cannot apply 0050_menu_db_generation: required table "
            "'stores' is missing. Restore the canonical baseline before retrying."
        )
    columns = {item["name"] for item in inspector.get_columns("stores")}
    if "menu_generation" not in columns:
        op.add_column("stores", sa.Column(
            "menu_generation", sa.BigInteger(), nullable=False,
            server_default=sa.text(str(GENERATION_BASELINE)),
            comment="Durable menu cache generation; MySQL is authoritative",
        ))
    if "menu_schedule_id" not in columns:
        op.add_column("stores", sa.Column(
            "menu_schedule_id", sa.BigInteger(), nullable=False,
            server_default=sa.text("-1"),
            comment="-1 means no active category schedule",
        ))
    conn.execute(
        sa.text("UPDATE stores SET menu_generation = :baseline WHERE menu_generation < :baseline"),
        {"baseline": GENERATION_BASELINE},
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    # Some legacy/partial schemas do not contain the optional stores table.
    # Rolling back this revision must not fail while inspecting an absent table.
    if not inspector.has_table("stores"):
        return
    columns = {item["name"] for item in inspector.get_columns("stores")}
    if "menu_schedule_id" in columns:
        op.drop_column("stores", "menu_schedule_id")
    if "menu_generation" in columns:
        op.drop_column("stores", "menu_generation")

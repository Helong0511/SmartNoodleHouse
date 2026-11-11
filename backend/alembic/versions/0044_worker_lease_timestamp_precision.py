"""后台 worker claim 租约时间戳升级到微秒精度。

Revision ID: 0044_worker_lease_timestamp_precision
Revises: 0043_delivery_refund_fingerprint
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0044_worker_lease_ts"
down_revision = "0043_delivery_refund_fingerprint"
branch_labels = None
depends_on = None

LEASE_TABLES = ("outbox_events", "webhook_inbox")


def _existing_lease_tables(conn, *, require_complete: bool = False):
    inspector = sa.inspect(conn)
    existing = []
    for table in LEASE_TABLES:
        if not inspector.has_table(table):
            if require_complete:
                raise RuntimeError(
                    f"Cannot apply 0044_worker_lease_timestamp_precision: "
                    f"required table '{table}' is missing. Restore the canonical baseline before retrying."
                )
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        if "locked_at" not in columns:
            if require_complete:
                raise RuntimeError(
                    f"Cannot apply 0044_worker_lease_timestamp_precision: "
                    f"required column '{table}.locked_at' is missing."
                )
            continue
        existing.append(table)
    # Return only after validating every table, so callers cannot mutate the
    # first table before discovering that a later required table is absent.
    return existing


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    for table in _existing_lease_tables(conn, require_complete=True):
        op.alter_column(
            table,
            "locked_at",
            existing_type=mysql.DATETIME(),
            type_=mysql.DATETIME(fsp=6),
            existing_nullable=True,
            nullable=True,
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    for table in _existing_lease_tables(conn):
        op.alter_column(
            table,
            "locked_at",
            existing_type=mysql.DATETIME(fsp=6),
            type_=mysql.DATETIME(),
            existing_nullable=True,
            nullable=True,
        )

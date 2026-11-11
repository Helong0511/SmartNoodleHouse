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


def _existing_lease_tables(conn):
    inspector = sa.inspect(conn)
    for table in LEASE_TABLES:
        if not inspector.has_table(table):
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        if "locked_at" in columns:
            yield table


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    for table in _existing_lease_tables(conn):
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

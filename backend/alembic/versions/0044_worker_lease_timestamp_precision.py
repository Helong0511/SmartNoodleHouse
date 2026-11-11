"""后台 worker claim 租约时间戳升级到微秒精度。

Revision ID: 0044_worker_lease_timestamp_precision
Revises: 0043_delivery_refund_fingerprint
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "0044_worker_lease_ts"
down_revision = "0043_delivery_refund_fingerprint"
branch_labels = None
depends_on = None


LEASE_TABLES = ("outbox_events", "webhook_inbox")


def upgrade() -> None:
    # MySQL DATETIME 默认精度为秒。心跳在同一秒内更新时，MySQL 默认
    # affected-rows 语义可能返回 rowcount=0，worker 会误判租约丢失并取消任务。
    # 微秒精度确保 locked_at 心跳值实际变化；SQLite 保留原生 DateTime 行为。
    conn = op.get_bind()
    if conn.dialect.name == "mysql":
        inspector = sa.inspect(conn)
        for table in LEASE_TABLES:
            if not inspector.has_table(table):
                continue
            columns = {item["name"] for item in inspector.get_columns(table)}
            if "locked_at" not in columns:
                continue
            op.alter_column(
                table,
                "locked_at",
                existing_type=sa.dialects.mysql.DATETIME(),
                type_=sa.dialects.mysql.DATETIME(fsp=6),
                existing_nullable=True,
                nullable=True,
            )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return

    inspector = sa.inspect(conn)
    existing_tables = []
    fractional_tables = []
    # Preflight every table before changing either one. DATETIME(0) conversion
    # truncates microseconds, which can change lease ordering and fencing behavior.
    for table in LEASE_TABLES:
        if not inspector.has_table(table):
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        if "locked_at" not in columns:
            continue
        existing_tables.append(table)
        has_fractional = conn.execute(
            text(
                f"SELECT 1 FROM `{table}` "
                "WHERE locked_at IS NOT NULL AND MICROSECOND(locked_at) <> 0 LIMIT 1"
            )
        ).scalar()
        if has_fractional:
            fractional_tables.append(table)

    if fractional_tables:
        names = ", ".join(fractional_tables)
        raise RuntimeError(
            "Refusing to downgrade worker lease timestamps to second precision: "
            f"microsecond values exist in {names}. Preserve DATETIME(6) to avoid "
            "truncating lease timestamps and changing worker ownership timing."
        )

    for table in existing_tables:
        op.alter_column(
            table,
            "locked_at",
            existing_type=sa.dialects.mysql.DATETIME(fsp=6),
            type_=sa.dialects.mysql.DATETIME(),
            existing_nullable=True,
            nullable=True,
        )

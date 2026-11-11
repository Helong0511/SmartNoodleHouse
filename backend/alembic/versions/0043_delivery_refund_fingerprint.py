"""外卖退款无平台退款编号时增加事件指纹去重。

Revision ID: 0043_delivery_refund_fingerprint
Revises: 0042_privacy_opt_in_default
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text

revision = "0043_delivery_refund_fingerprint"
down_revision = "0042_privacy_opt_in_default"
branch_labels = None
depends_on = None


def _column_exists(conn) -> bool:
    if conn.dialect.name == "sqlite":
        return any(row[1] == "event_fingerprint" for row in conn.execute(text("PRAGMA table_info(delivery_refunds)")).fetchall())
    return bool(conn.execute(text(
        "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='delivery_refunds' AND COLUMN_NAME='event_fingerprint'"
    )).scalar())


def _index_exists(conn) -> bool:
    name = "uq_delivery_refund_event_fingerprint"
    if conn.dialect.name == "sqlite":
        return any(row[1] == name for row in conn.execute(text("PRAGMA index_list(delivery_refunds)")).fetchall())
    return bool(conn.execute(text(
        "SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='delivery_refunds' AND INDEX_NAME=:name"
    ), {"name": name}).scalar())


def upgrade() -> None:
    conn = op.get_bind()
    # This table is part of the canonical schema. Do not advance Alembic
    # while the refund callback deduplication table is absent.
    if not inspect(conn).has_table("delivery_refunds"):
        raise RuntimeError(
            "Cannot apply 0043_delivery_refund_fingerprint: required table "
            "'delivery_refunds' is missing. Restore the canonical baseline before retrying."
        )
    if not _column_exists(conn):
        if conn.dialect.name == "sqlite":
            conn.execute(text("ALTER TABLE delivery_refunds ADD COLUMN event_fingerprint VARCHAR(64)"))
        else:
            conn.execute(text(
                "ALTER TABLE delivery_refunds ADD COLUMN event_fingerprint VARCHAR(64) NULL "
                "DEFAULT NULL COMMENT '平台无退款单号时的规范化事件指纹'"
            ))
    if not _index_exists(conn):
        conn.execute(text(
            "CREATE UNIQUE INDEX uq_delivery_refund_event_fingerprint "
            "ON delivery_refunds (platform, event_fingerprint)"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if not inspect(conn).has_table("delivery_refunds"):
        return
    if _index_exists(conn):
        if conn.dialect.name == "sqlite":
            conn.execute(text("DROP INDEX IF EXISTS uq_delivery_refund_event_fingerprint"))
        else:
            conn.execute(text("DROP INDEX uq_delivery_refund_event_fingerprint ON delivery_refunds"))
    # SQLite 跨版本 DROP COLUMN 行为不同，保留空列；不影响旧代码。
    if conn.dialect.name != "sqlite" and _column_exists(conn):
        conn.execute(text("ALTER TABLE delivery_refunds DROP COLUMN event_fingerprint"))

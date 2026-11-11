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

INDEX_NAME = "uq_delivery_refund_event_fingerprint"
INDEX_COLUMNS = ("platform", "event_fingerprint")


def _column_exists(conn) -> bool:
    if conn.dialect.name == "sqlite":
        return any(
            row[1] == "event_fingerprint"
            for row in conn.execute(text("PRAGMA table_info(delivery_refunds)")).fetchall()
        )
    return bool(conn.execute(text(
        "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='delivery_refunds' AND COLUMN_NAME='event_fingerprint'"
    )).scalar())


def _index_state(conn) -> tuple[bool, bool]:
    """Return (same-name index exists, index enforces the exact required unique key)."""
    if conn.dialect.name == "sqlite":
        indexes = conn.execute(text("PRAGMA index_list(delivery_refunds)")).fetchall()
        for item in indexes:
            if str(item[1]) != INDEX_NAME:
                continue
            columns = conn.execute(
                text(f"PRAGMA index_info('{INDEX_NAME}')")
            ).fetchall()
            actual_columns = tuple(str(column[2]) for column in columns)
            valid = bool(item[2]) and not bool(item[4]) and actual_columns == INDEX_COLUMNS
            return True, valid
        return False, False

    rows = conn.execute(text(
        "SELECT NON_UNIQUE, COLUMN_NAME, SEQ_IN_INDEX, SUB_PART "
        "FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='delivery_refunds' "
        "AND INDEX_NAME=:name ORDER BY SEQ_IN_INDEX"
    ), {"name": INDEX_NAME}).fetchall()
    if not rows:
        return False, False
    actual_columns = tuple(str(row[1]) for row in rows)
    valid = (
        all(int(row[0]) == 0 and row[3] is None for row in rows)
        and actual_columns == INDEX_COLUMNS
        and [int(row[2]) for row in rows] == list(range(1, len(INDEX_COLUMNS) + 1))
    )
    return True, valid


def upgrade() -> None:
    conn = op.get_bind()
    # delivery_refunds is required for idempotent refund callback processing.
    # Never record this revision as applied while the canonical table is absent.
    if not inspect(conn).has_table("delivery_refunds"):
        raise RuntimeError(
            "Cannot apply 0043_delivery_refund_fingerprint: required table "
            "'delivery_refunds' is missing. Restore the canonical baseline before retrying."
        )
    if not _column_exists(conn):
        if conn.dialect.name == "sqlite":
            conn.execute(text(
                "ALTER TABLE delivery_refunds ADD COLUMN event_fingerprint VARCHAR(64)"
            ))
        else:
            conn.execute(text(
                "ALTER TABLE delivery_refunds ADD COLUMN event_fingerprint VARCHAR(64) NULL "
                "DEFAULT NULL COMMENT '平台无退款单号时的规范化事件指纹'"
            ))

    index_exists, index_valid = _index_state(conn)
    if index_valid:
        return
    if index_exists:
        # A same-name but non-unique/wrong-column/prefix index does not provide
        # idempotency. Replace only that index; duplicate data still makes the
        # following UNIQUE creation fail closed rather than deleting refunds.
        if conn.dialect.name == "sqlite":
            conn.execute(text(f"DROP INDEX {INDEX_NAME}"))
        else:
            conn.execute(text(
                f"DROP INDEX {INDEX_NAME} ON delivery_refunds"
            ))
    conn.execute(text(
        "CREATE UNIQUE INDEX uq_delivery_refund_event_fingerprint "
        "ON delivery_refunds (platform, event_fingerprint)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    if not inspect(conn).has_table("delivery_refunds"):
        return
    index_exists, _ = _index_state(conn)
    if index_exists:
        if conn.dialect.name == "sqlite":
            conn.execute(text(f"DROP INDEX {INDEX_NAME}"))
        else:
            conn.execute(text(f"DROP INDEX {INDEX_NAME} ON delivery_refunds"))
    # SQLite 跨版本 DROP COLUMN 行为不同，保留空列；不影响旧代码。
    if conn.dialect.name != "sqlite" and _column_exists(conn):
        conn.execute(text("ALTER TABLE delivery_refunds DROP COLUMN event_fingerprint"))

"""visitor_events 增加 customer_id，会员识别可落库顾客信息。

Revision ID: 0039_visitor_event_customer
Revises: 0038_coupon_tmpl_annual_md
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0039_visitor_event_customer"
down_revision = "0038_coupon_tmpl_annual_md"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    if conn.dialect.name == "sqlite":
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return any(r[1] == column for r in rows)
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    ).scalar()
    return bool(row)


def _index_exists(conn, table: str, index: str) -> bool:
    if conn.dialect.name == "sqlite":
        rows = conn.execute(text(f"PRAGMA index_list({table})")).fetchall()
        return any(r[1] == index for r in rows)
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
        ),
        {"t": table, "i": index},
    ).scalar()
    return bool(row)


def _fk_exists(conn, table: str, fk_name: str) -> bool:
    if conn.dialect.name == "sqlite":
        return False
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.TABLE_CONSTRAINTS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t "
            "AND CONSTRAINT_TYPE = 'FOREIGN KEY' AND CONSTRAINT_NAME = :n"
        ),
        {"t": table, "n": fk_name},
    ).scalar()
    return bool(row)


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "visitor_events", "customer_id"):
        if conn.dialect.name == "sqlite":
            conn.execute(text("ALTER TABLE visitor_events ADD COLUMN customer_id INTEGER"))
        else:
            conn.execute(
                text(
                    "ALTER TABLE visitor_events ADD COLUMN customer_id "
                    "bigint UNSIGNED NULL DEFAULT NULL "
                    "COMMENT '命中授权会员时 → customers.id；陌生客为空' AFTER camera_id"
                )
            )
    if not _index_exists(conn, "visitor_events", "idx_ve_customer_time"):
        if conn.dialect.name == "sqlite":
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_ve_customer_time "
                    "ON visitor_events (customer_id, event_time)"
                )
            )
        else:
            conn.execute(
                text(
                    "ALTER TABLE visitor_events ADD INDEX idx_ve_customer_time "
                    "(customer_id ASC, event_time ASC)"
                )
            )
    if conn.dialect.name != "sqlite" and not _fk_exists(conn, "visitor_events", "fk_ve_customer"):
        conn.execute(
            text(
                "ALTER TABLE visitor_events ADD CONSTRAINT fk_ve_customer "
                "FOREIGN KEY (customer_id) REFERENCES customers (id) "
                "ON DELETE SET NULL ON UPDATE RESTRICT"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "sqlite" and _fk_exists(conn, "visitor_events", "fk_ve_customer"):
        conn.execute(text("ALTER TABLE visitor_events DROP FOREIGN KEY fk_ve_customer"))
    if _index_exists(conn, "visitor_events", "idx_ve_customer_time"):
        if conn.dialect.name == "sqlite":
            conn.execute(text("DROP INDEX IF EXISTS idx_ve_customer_time"))
        else:
            conn.execute(text("ALTER TABLE visitor_events DROP INDEX idx_ve_customer_time"))
    if _column_exists(conn, "visitor_events", "customer_id"):
        if conn.dialect.name == "sqlite":
            # SQLite 旧版本无 DROP COLUMN；保留列无害
            pass
        else:
            conn.execute(text("ALTER TABLE visitor_events DROP COLUMN customer_id"))

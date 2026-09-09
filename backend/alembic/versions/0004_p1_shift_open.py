"""P1 hardening: cashier_shifts one OPEN per operator

Revision ID: 0004_p1_shift_open
Revises: 0003_p1_hardening
Create Date: 2026-09-09
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0004_p1_shift_open"
down_revision = "0003_p1_hardening"
branch_labels = None
depends_on = None


def _scalar(conn, sql: str, params: dict | None = None) -> int:
    row = conn.execute(text(sql), params or {}).scalar()
    return int(row or 0)


def _table_exists(conn, table: str) -> bool:
    return (
        _scalar(
            conn,
            """
            SELECT COUNT(*) FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t
            """,
            {"t": table},
        )
        > 0
    )


def _column_exists(conn, table: str, column: str) -> bool:
    return (
        _scalar(
            conn,
            """
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c
            """,
            {"t": table, "c": column},
        )
        > 0
    )


def _index_exists(conn, table: str, index: str) -> bool:
    return (
        _scalar(
            conn,
            """
            SELECT COUNT(*) FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i
            """,
            {"t": table, "i": index},
        )
        > 0
    )


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "cashier_shifts"):
        return
    if not _column_exists(conn, "cashier_shifts", "open_operator_key"):
        conn.execute(
            text(
                """
                ALTER TABLE cashier_shifts
                  ADD COLUMN open_operator_key BIGINT UNSIGNED
                    GENERATED ALWAYS AS (IF(status = 'OPEN', operator_id, NULL)) STORED
                    COMMENT 'OPEN 时为 operator_id，CLOSED 为 NULL；用于唯一约束'
                """
            )
        )
    # 关闭重复 OPEN，保留最大 id
    conn.execute(
        text(
            """
            UPDATE cashier_shifts cs
            INNER JOIN (
              SELECT operator_id, MAX(id) AS keep_id
              FROM cashier_shifts
              WHERE status = 'OPEN'
              GROUP BY operator_id
              HAVING COUNT(*) > 1
            ) d ON d.operator_id = cs.operator_id
            SET cs.status = 'CLOSED',
                cs.closed_at = COALESCE(cs.closed_at, NOW(3)),
                cs.remark = CONCAT(COALESCE(cs.remark, ''), ' [auto-close duplicate OPEN]')
            WHERE cs.status = 'OPEN' AND cs.id <> d.keep_id
            """
        )
    )
    if not _index_exists(conn, "cashier_shifts", "uk_cashier_shift_one_open"):
        conn.execute(
            text(
                "ALTER TABLE cashier_shifts ADD UNIQUE INDEX uk_cashier_shift_one_open (open_operator_key)"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "cashier_shifts"):
        return
    if _index_exists(conn, "cashier_shifts", "uk_cashier_shift_one_open"):
        conn.execute(text("ALTER TABLE cashier_shifts DROP INDEX uk_cashier_shift_one_open"))
    if _column_exists(conn, "cashier_shifts", "open_operator_key"):
        conn.execute(text("ALTER TABLE cashier_shifts DROP COLUMN open_operator_key"))

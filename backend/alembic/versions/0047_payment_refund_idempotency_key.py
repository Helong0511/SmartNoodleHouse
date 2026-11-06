"""为已部署数据库补齐退款幂等键字段及唯一约束。

Revision ID: 0047_payment_refund_idempotency_key
Revises: 0046_outbox_stock_sync_version

基线迁移会读取当前 sql/struct.sql。已经执行过旧版基线的数据库不会自动获得后来
加入 struct.sql 的列，因此这里做幂等式 schema upgrade；绝不自动删除历史退款记录。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0047_payment_refund_idem"
down_revision = "0046_outbox_stock_sync_version"
branch_labels = None
depends_on = None

INDEX_NAME = "uk_refund_idempotency"


def _column_exists(conn) -> bool:
    if conn.dialect.name == "sqlite":
        rows = conn.exec_driver_sql("PRAGMA table_info(payment_refunds)").fetchall()
        return any(str(row[1]) == "idempotency_key" for row in rows)
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'payment_refunds' "
                "AND COLUMN_NAME = 'idempotency_key'"
            )
        ).scalar()
    )


def _unique_single_column_index_exists(conn) -> bool:
    if conn.dialect.name == "sqlite":
        indexes = conn.exec_driver_sql(
            "PRAGMA index_list(payment_refunds)"
        ).fetchall()
        for index in indexes:
            # SQLite: seq, name, unique, origin, partial
            if not bool(index[2]):
                continue
            name = str(index[1]).replace("'", "''")
            cols = conn.exec_driver_sql(
                f"PRAGMA index_info('{name}')"
            ).fetchall()
            if len(cols) == 1 and str(cols[0][2]) == "idempotency_key":
                return True
        return False

    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'payment_refunds' "
                "AND NON_UNIQUE = 0 "
                "GROUP BY INDEX_NAME "
                "HAVING COUNT(*) = 1 "
                "AND SUM(CASE WHEN COLUMN_NAME = 'idempotency_key' THEN 1 ELSE 0 END) = 1 "
                "LIMIT 1"
            )
        ).first()
    )


def _assert_no_duplicate_keys(conn) -> None:
    duplicate = conn.execute(
        sa.text(
            "SELECT idempotency_key, COUNT(*) AS row_count "
            "FROM payment_refunds "
            "WHERE idempotency_key IS NOT NULL "
            "GROUP BY idempotency_key "
            "HAVING COUNT(*) > 1 "
            "LIMIT 1"
        )
    ).first()
    if duplicate:
        key = str(duplicate[0])[:12]
        count = int(duplicate[1])
        raise RuntimeError(
            "payment_refunds.idempotency_key 存在重复历史值，"
            f"拒绝自动删除退款数据；请先人工核对 key 前缀={key!r}、重复数={count}"
        )


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn):
        op.add_column(
            "payment_refunds",
            sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        )

    if not _unique_single_column_index_exists(conn):
        _assert_no_duplicate_keys(conn)
        op.create_index(
            INDEX_NAME,
            "payment_refunds",
            ["idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    conn = op.get_bind()
    # 该字段已被 API/服务用于防止重复退款。降级时保留数据列和唯一索引，
    # 避免回滚代码时破坏历史幂等记录；如需移除应另行制定数据迁移方案。
    return

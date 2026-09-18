"""审计日志：新增 category 与 store_id 列、加索引

Revision ID: 0016_audit_log_category_store
Revises: 0015_delivery_menu_ops
Create Date: 2026-09-17
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0016_audit_log_category_store"
down_revision = "0015_delivery_menu_ops"
branch_labels = None
depends_on = None


def _col_exists(conn, table: str, column: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = bind.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == column for r in rows)
    row = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c
            """
        ),
        {"t": table, "c": column},
    ).scalar()
    return bool(row)


def _index_exists(conn, index_name: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = bind.exec_driver_sql(f"PRAGMA index_list(operation_logs)").fetchall()
        return any(r[1] == index_name for r in rows)
    row = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = 'operation_logs' "
            "AND index_name = :n"
        ),
        {"n": index_name},
    ).scalar()
    return bool(row)


def upgrade() -> None:
    bind = op.get_bind()
    conn = bind
    if not _col_exists(conn, "operation_logs", "category"):
        op.add_column(
            "operation_logs",
            sa.Column("category", sa.String(length=20), nullable=True, comment="审计分类"),
        )
    if not _col_exists(conn, "operation_logs", "store_id"):
        op.add_column(
            "operation_logs",
            sa.Column("store_id", sa.BigInteger(), nullable=True, comment="操作关联门店"),
        )
    # 回填：历史数据按 module/action 推断分类
    conn.execute(
        sa.text(
            "UPDATE operation_logs SET category='SYSTEM' WHERE category IS NULL "
            "AND module IN ('system_settings','settings','system','auth','security','scheduler')"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE operation_logs SET category='CUSTOMER' WHERE category IS NULL "
            "AND module IN ('face','customer','miniprogram','member')"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE operation_logs SET category='STORE_ADMIN' WHERE category IS NULL "
            "AND module IN ('table','coupon','store','member_level')"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE operation_logs SET category='CASHIER' WHERE category IS NULL "
            "AND module IN ('pos','payment','order')"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE operation_logs SET category='KITCHEN' WHERE category IS NULL "
            "AND module IN ('kitchen','print','order_kitchen')"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE operation_logs SET category='STAFF' WHERE category IS NULL"
        )
    )
    # 索引：便于按分类、门店、时间范围查询
    if not _index_exists(conn, "ix_operation_logs_category"):
        op.create_index("ix_operation_logs_category", "operation_logs", ["category"])
    if not _index_exists(conn, "ix_operation_logs_store_id"):
        op.create_index("ix_operation_logs_store_id", "operation_logs", ["store_id"])
    if not _index_exists(conn, "ix_operation_logs_created_at"):
        op.create_index("ix_operation_logs_created_at", "operation_logs", ["created_at"])


def downgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, "ix_operation_logs_created_at"):
        op.drop_index("ix_operation_logs_created_at", table_name="operation_logs")
    if _index_exists(conn, "ix_operation_logs_store_id"):
        op.drop_index("ix_operation_logs_store_id", table_name="operation_logs")
    if _index_exists(conn, "ix_operation_logs_category"):
        op.drop_index("ix_operation_logs_category", table_name="operation_logs")
    # SQLite 不支持 drop column；保留列
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.drop_column("operation_logs", "store_id")
        op.drop_column("operation_logs", "category")

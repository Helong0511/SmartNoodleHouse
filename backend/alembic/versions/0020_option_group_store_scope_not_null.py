"""option_groups.store_scope 加 NOT NULL（与 ORM nullable=False 对齐）

Revision ID: 0020_option_group_store_scope_not_null
Revises: 0019_three_way_alignment
Create Date: 2026-09-21
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0020_store_scope_not_null"
down_revision = "0019_three_way_alignment"
branch_labels = None
depends_on = None


def _col_nullable(conn, table: str, col: str) -> bool | None:
    """返回列是否可空；列不存在返回 None。"""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        for r in rows:
            if r[1] == col:
                return r[3] == 0  # notnull 列
        return None
    row = conn.execute(
        text(
            """
            SELECT IS_NULLABLE FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = :t AND column_name = :c
            """
        ),
        {"t": table, "c": col},
    ).scalar()
    if row is None:
        return None
    return (row or "").upper() == "YES"


def upgrade() -> None:
    conn = op.get_bind()
    # 仅 MySQL：SQLite 不支持 MODIFY GENERATED 列
    if conn.dialect.name == "sqlite":
        return
    nullable = _col_nullable(conn, "option_groups", "store_scope")
    if nullable is None:
        # 列不存在，跳过（0011 未执行或被回滚）
        return
    if not nullable:
        # 已是 NOT NULL，幂等跳过
        return
    # MODIFY GENERATED STORED 列需重新指定表达式；IFNULL(store_id, 0) 永不为 NULL，加 NOT NULL 安全
    # MySQL 语法：NOT NULL 必须放在 STORED 之后
    conn.execute(
        text(
            "ALTER TABLE option_groups "
            "MODIFY COLUMN store_scope BIGINT UNSIGNED "
            "GENERATED ALWAYS AS (IFNULL(store_id, 0)) STORED NOT NULL"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        return
    nullable = _col_nullable(conn, "option_groups", "store_scope")
    if nullable is None or nullable:
        return
    conn.execute(
        text(
            "ALTER TABLE option_groups "
            "MODIFY COLUMN store_scope BIGINT UNSIGNED "
            "GENERATED ALWAYS AS (IFNULL(store_id, 0)) STORED NULL"
        )
    )

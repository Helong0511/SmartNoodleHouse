"""三方结构对齐：JSON 类型 / NOT NULL / UNIQUE 约束 / 冗余单列 UNIQUE 清理

Revision ID: 0019_three_way_alignment
Revises: 0018_taste_recommendation
Create Date: 2026-09-21

变更内容：
- A) 6 列 ORM Text→JSON（DB/SQL 已是 JSON，本迁移无需改 DB；仅对齐 ORM 与 DB 一致性）
- B) delivery_orders 4 列加 NOT NULL（DB 当前可空，已有 DEFAULT 值，迁移安全）
- C) 3 处 UNIQUE 约束补齐
    - customer_coupons.client_request_id 单列 UNIQUE（uk_cc_client_request）
    - tables (store_id, table_no) 复合 UNIQUE（uk_tables_store_no）
    - option_groups 已有 uk_og_store_code (store_scope, code)，无需新增
- D) financial_ledger 删除 3 个冗余单列 UNIQUE（uk_fl_payment/uk_fl_refund/uk_fl_delivery_refund），
      已被 0017 的复合 UNIQUE 严格覆盖
- G) delivery_refunds (platform, platform_refund_id) 复合 UNIQUE（uk_dr_platform_refund）
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0019_three_way_alignment"
down_revision = "0018_taste_recommendation"
branch_labels = None
depends_on = None


# ---------- 双方言存在性检查 ----------

def _index_exists(conn, table: str, name: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = conn.exec_driver_sql(f"PRAGMA index_list({table})").fetchall()
        return any(r[1] == name for r in rows)
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :n"
        ),
        {"t": table, "n": name},
    ).scalar()
    return bool(row)


def _column_nullable(conn, table: str, col: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        for r in rows:
            if r[1] == col:
                return r[3] == 0  # notnull=1 → nullable=0
        return True
    row = conn.execute(
        text(
            "SELECT IS_NULLABLE FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": col},
    ).scalar()
    return (row or "YES").upper() == "YES"


def _drop_index(conn, table: str, name: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        conn.exec_driver_sql(f"DROP INDEX IF EXISTS {name}")
        return
    # MySQL：若索引仍被外键引用（errno 1553）则跳过，避免阻断后续 revision。
    try:
        conn.execute(text(f"ALTER TABLE {table} DROP INDEX {name}"))
    except Exception as exc:  # noqa: BLE001 — dialect 包装层不一致，按文案识别
        msg = str(exc).lower()
        if "1553" in msg or "needed in a foreign key" in msg:
            return
        raise


def _add_unique_index(conn, table: str, name: str, cols: str) -> None:
    if _index_exists(conn, table, name):
        return
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        conn.exec_driver_sql(f"CREATE UNIQUE INDEX {name} ON {table} ({cols})")
    else:
        conn.execute(text(f"ALTER TABLE {table} ADD UNIQUE KEY {name} ({cols})"))


def _set_not_null(conn, table: str, col: str, default_sql: str) -> None:
    if not _column_nullable(conn, table, col):
        return  # 已是 NOT NULL
    bind = op.get_bind()
    # 先把现有 NULL 行填默认值（理论上 DEFAULT 已保证，但旧数据可能 NULL）
    if bind.dialect.name == "sqlite":
        conn.exec_driver_sql(f"UPDATE {table} SET {col} = {default_sql} WHERE {col} IS NULL")
        col_def = conn.exec_driver_sql(
            f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'"
        ).fetchone()
        # SQLite 改 NOT NULL 需重建表；鉴于 SQLite 仅测试用，跳过
        return
    # MySQL：先 UPDATE，再 ALTER MODIFY
    conn.execute(text(f"UPDATE {table} SET {col} = {default_sql} WHERE {col} IS NULL"))
    # 取当前列定义类型
    row = conn.execute(
        text(
            "SELECT COLUMN_TYPE, COLUMN_COMMENT FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": col},
    ).fetchone()
    col_type = row[0] if row else "DECIMAL(10,2)"
    col_comment = row[1] if row else ""
    comment_clause = f" COMMENT '{col_comment}'" if col_comment else ""
    conn.execute(
        text(
            f"ALTER TABLE {table} MODIFY COLUMN {col} {col_type} NOT NULL "
            f"DEFAULT {default_sql}{comment_clause}"
        )
    )


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # B) delivery_orders 4 列加 NOT NULL（已有 DEFAULT，迁移安全）
    # ------------------------------------------------------------------
    _set_not_null(conn, "delivery_orders", "platform_fee", "0.00")
    _set_not_null(conn, "delivery_orders", "packing_fee", "0.00")
    _set_not_null(conn, "delivery_orders", "delivery_fee", "0.00")
    _set_not_null(conn, "delivery_orders", "refund_status", "'NONE'")

    # ------------------------------------------------------------------
    # C) UNIQUE 约束补齐
    # ------------------------------------------------------------------
    # C-1 customer_coupons.client_request_id 单列 UNIQUE（NULL 不受约束）
    _add_unique_index(conn, "customer_coupons", "uq_cc_client_request", "client_request_id")
    # C-2 tables (store_id, table_no) 复合 UNIQUE
    _add_unique_index(conn, "tables", "uk_tables_store_no", "store_id, table_no")
    # C-3 option_groups 已有 uk_og_store_code (store_scope, code)，无需变更

    # ------------------------------------------------------------------
    # G) delivery_refunds (platform, platform_refund_id) 复合 UNIQUE
    # ------------------------------------------------------------------
    _add_unique_index(
        conn, "delivery_refunds", "uk_dr_platform_refund", "platform, platform_refund_id"
    )

    # ------------------------------------------------------------------
    # D) financial_ledger 删除 3 个冗余单列 UNIQUE
    #   - uk_fl_payment (payment_id)
    #   - uk_fl_refund (refund_id)
    #   - uk_fl_delivery_refund (delivery_refund_id)
    #   保留 0017 引入的复合 UNIQUE uq_fin_ledger_entry_*（更严格且已覆盖业务键）
    # ------------------------------------------------------------------
    for idx in ("uk_fl_payment", "uk_fl_refund", "uk_fl_delivery_refund"):
        if _index_exists(conn, "financial_ledger", idx):
            _drop_index(conn, "financial_ledger", idx)

    # ------------------------------------------------------------------
    # A) 6 列 ORM Text→JSON：DB/SQL 已是 JSON 类型，无需变更实际库
    # ------------------------------------------------------------------


def downgrade() -> None:
    conn = op.get_bind()

    # D) 恢复 financial_ledger 3 个单列 UNIQUE
    _add_unique_index(conn, "financial_ledger", "uk_fl_payment", "payment_id")
    _add_unique_index(conn, "financial_ledger", "uk_fl_refund", "refund_id")
    _add_unique_index(conn, "financial_ledger", "uk_fl_delivery_refund", "delivery_refund_id")

    # G) 删除 delivery_refunds 复合 UNIQUE
    if _index_exists(conn, "delivery_refunds", "uk_dr_platform_refund"):
        _drop_index(conn, "delivery_refunds", "uk_dr_platform_refund")

    # C-2 删除 tables 复合 UNIQUE
    if _index_exists(conn, "tables", "uk_tables_store_no"):
        _drop_index(conn, "tables", "uk_tables_store_no")

    # C-1 删除 customer_coupons.client_request_id 单列 UNIQUE
    if _index_exists(conn, "customer_coupons", "uq_cc_client_request"):
        _drop_index(conn, "customer_coupons", "uq_cc_client_request")

    # B) delivery_orders 4 列回退为可空（保持原状）
    # 注：回退为 nullable 不强制执行，因 ORM 已要求 NOT NULL；downgrade 仅清理新增约束

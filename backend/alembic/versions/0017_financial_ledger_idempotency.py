"""财务分录幂等唯一约束：防并发回调重复入账

Revision ID: 0017_fin_ledger_idempotency
Revises: 0016_audit_log_category_store
Create Date: 2026-09-17
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0017_fin_ledger_idempotency"
down_revision = "0016_audit_log_category_store"
branch_labels = None
depends_on = None

# (索引名, entry_type, 业务键列) — MySQL/SQLite 唯一索引对 NULL 不生效，业务键为 NULL 的行不受限
_CONSTRAINTS = (
    ("uq_fin_ledger_entry_payment", "SALE", "payment_id"),
    ("uq_fin_ledger_entry_refund", "REFUND", "refund_id"),
    ("uq_fin_ledger_entry_delivery_order", "SALE", "delivery_order_id"),
    ("uq_fin_ledger_entry_delivery_refund", "REFUND", "delivery_refund_id"),
)


def _index_exists(conn, index_name: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = bind.exec_driver_sql("PRAGMA index_list(financial_ledger)").fetchall()
        return any(r[1] == index_name for r in rows)
    row = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = 'financial_ledger' "
            "AND index_name = :n"
        ),
        {"n": index_name},
    ).scalar()
    return bool(row)


def upgrade() -> None:
    conn = op.get_bind()
    # 建索引前去重：保留每组业务键最早一行，避免历史重复数据导致索引创建失败
    for _idx, entry_type, col in _CONSTRAINTS:
        if conn.dialect.name == "sqlite":
            conn.execute(
                sa.text(
                    f"DELETE FROM financial_ledger WHERE entry_type = '{entry_type}' "
                    f"AND {col} IS NOT NULL AND id NOT IN ("
                    f"SELECT MIN(id) FROM financial_ledger WHERE entry_type = '{entry_type}' "
                    f"AND {col} IS NOT NULL GROUP BY {col})"
                )
            )
        else:
            conn.execute(
                sa.text(
                    f"DELETE f1 FROM financial_ledger f1 JOIN financial_ledger f2 "
                    f"ON f1.entry_type = f2.entry_type AND f1.{col} = f2.{col} "
                    f"AND f1.id > f2.id WHERE f1.entry_type = :et AND f1.{col} IS NOT NULL"
                ),
                {"et": entry_type},
            )
    for idx, entry_type, col in _CONSTRAINTS:
        if not _index_exists(conn, idx):
            # 唯一索引对 NULL 不生效（MySQL/SQLite 语义一致）：业务键为 NULL 的行不受限
            op.create_index(idx, "financial_ledger", ["entry_type", col], unique=True)


def downgrade() -> None:
    conn = op.get_bind()
    for idx, _et, _col in _CONSTRAINTS:
        if _index_exists(conn, idx):
            op.drop_index(idx, table_name="financial_ledger")

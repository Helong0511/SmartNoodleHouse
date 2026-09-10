"""对齐业务：去掉券每人每模板唯一；账本/积分增加幂等唯一索引

Revision ID: 0010_coupon_ledger_idempotency
Revises: 0009_product_checks
Create Date: 2026-09-10
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0010_coupon_ledger_idempotency"
down_revision = "0009_product_checks"
branch_labels = None
depends_on = None


def _index_exists(conn, table: str, name: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = :t AND index_name = :n
            """
        ),
        {"t": table, "n": name},
    ).scalar()
    return int(row or 0) > 0


def upgrade() -> None:
    conn = op.get_bind()
    # P1-3/4：取消 uk_cc_customer_template（勿再 DELETE 去重；与产品「同模板可多张」一致）
    if _index_exists(conn, "customer_coupons", "uk_cc_customer_template"):
        conn.execute(text("ALTER TABLE customer_coupons DROP INDEX uk_cc_customer_template"))
    if not _index_exists(conn, "customer_coupons", "idx_cc_customer_template"):
        conn.execute(
            text(
                "ALTER TABLE customer_coupons ADD INDEX idx_cc_customer_template (customer_id, template_id)"
            )
        )

    # financial_ledger：非空外键幂等（MySQL 允许多个 NULL）
    if not _index_exists(conn, "financial_ledger", "uk_fl_payment"):
        conn.execute(text("ALTER TABLE financial_ledger ADD UNIQUE INDEX uk_fl_payment (payment_id)"))
    if not _index_exists(conn, "financial_ledger", "uk_fl_refund"):
        conn.execute(text("ALTER TABLE financial_ledger ADD UNIQUE INDEX uk_fl_refund (refund_id)"))
    if not _index_exists(conn, "financial_ledger", "uk_fl_delivery_refund"):
        conn.execute(
            text(
                "ALTER TABLE financial_ledger ADD UNIQUE INDEX uk_fl_delivery_refund (delivery_refund_id)"
            )
        )

    # point_ledger：退款扣分按 refund_id；发放按生成列钉死单笔 EARN
    if not _index_exists(conn, "point_ledger", "uk_pl_refund"):
        conn.execute(text("ALTER TABLE point_ledger ADD UNIQUE INDEX uk_pl_refund (refund_id)"))
    # 生成列：仅 EARN 行暴露 order_id，保证每单至多一条发放流水
    col = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'point_ledger' AND column_name = 'earn_order_id'
            """
        )
    ).scalar()
    if int(col or 0) == 0:
        conn.execute(
            text(
                """
                ALTER TABLE point_ledger
                  ADD COLUMN earn_order_id BIGINT UNSIGNED
                    GENERATED ALWAYS AS (IF(entry_type = 'EARN', order_id, NULL)) STORED
                """
            )
        )
    if not _index_exists(conn, "point_ledger", "uk_pl_earn_order"):
        conn.execute(
            text("ALTER TABLE point_ledger ADD UNIQUE INDEX uk_pl_earn_order (earn_order_id)")
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, "point_ledger", "uk_pl_earn_order"):
        conn.execute(text("ALTER TABLE point_ledger DROP INDEX uk_pl_earn_order"))
    col = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'point_ledger' AND column_name = 'earn_order_id'
            """
        )
    ).scalar()
    if int(col or 0) > 0:
        conn.execute(text("ALTER TABLE point_ledger DROP COLUMN earn_order_id"))
    if _index_exists(conn, "point_ledger", "uk_pl_refund"):
        conn.execute(text("ALTER TABLE point_ledger DROP INDEX uk_pl_refund"))
    for name in ("uk_fl_delivery_refund", "uk_fl_refund", "uk_fl_payment"):
        if _index_exists(conn, "financial_ledger", name):
            conn.execute(text(f"ALTER TABLE financial_ledger DROP INDEX {name}"))
    # 不恢复 uk_cc_customer_template（与现行产品策略冲突）

"""P0 hardening: operator_id、webhook/refund 重试字段、ledger 表

Revision ID: 0002_p0_hardening
Revises: 0001_baseline
Create Date: 2026-09-09
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

# revision identifiers
revision = "0002_p0_hardening"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _scalar(conn, sql: str, params: dict | None = None) -> int:
    row = conn.execute(text(sql), params or {}).scalar()
    return int(row or 0)


def _column_exists(conn, table: str, column: str) -> bool:
    return (
        _scalar(
            conn,
            """
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND COLUMN_NAME = :column
            """,
            {"table": table, "column": column},
        )
        > 0
    )


def _index_exists(conn, table: str, index: str) -> bool:
    return (
        _scalar(
            conn,
            """
            SELECT COUNT(*) FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND INDEX_NAME = :index
            """,
            {"table": table, "index": index},
        )
        > 0
    )


def _fk_exists(conn, table: str, constraint: str) -> bool:
    return (
        _scalar(
            conn,
            """
            SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND CONSTRAINT_NAME = :name
              AND CONSTRAINT_TYPE = 'FOREIGN KEY'
            """,
            {"table": table, "name": constraint},
        )
        > 0
    )


def _table_exists(conn, table: str) -> bool:
    return (
        _scalar(
            conn,
            """
            SELECT COUNT(*) FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
            """,
            {"table": table},
        )
        > 0
    )


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    if not _column_exists(conn, table, column):
        conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN {ddl}"))


def upgrade() -> None:
    conn = op.get_bind()

    # --- orders.operator_id + FK + 索引；更新幂等键注释 ---
    if _table_exists(conn, "orders"):
        _add_column_if_missing(
            conn,
            "orders",
            "operator_id",
            "operator_id BIGINT UNSIGNED NULL COMMENT '下单/收银操作员 → users.id（POS 渠道必填）' AFTER idempotency_key",
        )
        if not _index_exists(conn, "orders", "idx_orders_operator"):
            conn.execute(text("ALTER TABLE orders ADD INDEX idx_orders_operator (operator_id)"))
        if not _fk_exists(conn, "orders", "fk_orders_operator"):
            conn.execute(
                text(
                    "ALTER TABLE orders "
                    "ADD CONSTRAINT fk_orders_operator FOREIGN KEY (operator_id) REFERENCES users(id)"
                )
            )
        conn.execute(
            text(
                "ALTER TABLE orders MODIFY COLUMN idempotency_key VARCHAR(64) NULL "
                "COMMENT '下单幂等键（按渠道/门店/操作员等作用域拼接，防重复提交；允许多 NULL）'"
            )
        )

    # --- webhook_inbox 重试/锁字段 ---
    if _table_exists(conn, "webhook_inbox"):
        _add_column_if_missing(
            conn,
            "webhook_inbox",
            "attempt_count",
            "attempt_count INT NOT NULL DEFAULT 0 COMMENT '消费尝试次数' AFTER error_message",
        )
        _add_column_if_missing(
            conn,
            "webhook_inbox",
            "last_error",
            "last_error VARCHAR(500) NULL COMMENT '最近一次错误摘要' AFTER attempt_count",
        )
        _add_column_if_missing(
            conn,
            "webhook_inbox",
            "next_retry_at",
            "next_retry_at DATETIME(3) NULL COMMENT '下次允许重试时间' AFTER last_error",
        )
        _add_column_if_missing(
            conn,
            "webhook_inbox",
            "locked_at",
            "locked_at DATETIME(3) NULL COMMENT '消费者加锁时间' AFTER next_retry_at",
        )
        _add_column_if_missing(
            conn,
            "webhook_inbox",
            "locked_by",
            "locked_by VARCHAR(64) NULL COMMENT '锁持有者标识（实例/worker）' AFTER locked_at",
        )
        if not _index_exists(conn, "webhook_inbox", "idx_wi_retry"):
            conn.execute(text("ALTER TABLE webhook_inbox ADD INDEX idx_wi_retry (status, next_retry_at)"))
        if not _index_exists(conn, "webhook_inbox", "idx_wi_locked"):
            conn.execute(text("ALTER TABLE webhook_inbox ADD INDEX idx_wi_locked (locked_at, locked_by)"))

    # --- payment_refunds 重试/幂等字段 ---
    if _table_exists(conn, "payment_refunds"):
        _add_column_if_missing(
            conn,
            "payment_refunds",
            "retry_count",
            "retry_count INT NOT NULL DEFAULT 0 COMMENT '主动查询/重试次数' AFTER operator_id",
        )
        _add_column_if_missing(
            conn,
            "payment_refunds",
            "next_retry_at",
            "next_retry_at DATETIME(3) NULL COMMENT '下次允许重试时间' AFTER retry_count",
        )
        _add_column_if_missing(
            conn,
            "payment_refunds",
            "last_error",
            "last_error VARCHAR(500) NULL COMMENT '最近一次失败摘要' AFTER next_retry_at",
        )
        _add_column_if_missing(
            conn,
            "payment_refunds",
            "idempotency_key",
            "idempotency_key VARCHAR(64) NULL COMMENT '退款幂等键（防重复发起）' AFTER last_error",
        )
        if not _index_exists(conn, "payment_refunds", "uk_refund_idempotency"):
            conn.execute(
                text("ALTER TABLE payment_refunds ADD UNIQUE KEY uk_refund_idempotency (idempotency_key)")
            )
        if not _index_exists(conn, "payment_refunds", "idx_refunds_retry"):
            conn.execute(
                text("ALTER TABLE payment_refunds ADD INDEX idx_refunds_retry (status, next_retry_at)")
            )

    # --- financial_ledger ---
    if not _table_exists(conn, "financial_ledger"):
        conn.execute(
            text(
                """
                CREATE TABLE financial_ledger (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
                  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
                  entry_type VARCHAR(20) NOT NULL COMMENT 'SALE=收款 | REFUND=退款 | ADJUSTMENT=调账',
                  channel VARCHAR(30) DEFAULT NULL COMMENT '业务渠道',
                  provider VARCHAR(20) DEFAULT NULL COMMENT '支付提供方',
                  amount DECIMAL(10,2) NOT NULL COMMENT '金额（元）',
                  currency VARCHAR(8) NOT NULL DEFAULT 'CNY' COMMENT '币种',
                  order_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联订单',
                  payment_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联支付单',
                  refund_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联退款单',
                  delivery_order_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联外卖扩展单',
                  delivery_refund_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联外卖退款',
                  remark VARCHAR(500) DEFAULT NULL COMMENT '备注',
                  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入账时间',
                  INDEX idx_fl_store_created (store_id, created_at),
                  INDEX idx_fl_entry_type (entry_type, created_at),
                  INDEX idx_fl_order (order_id),
                  INDEX idx_fl_payment (payment_id),
                  INDEX idx_fl_refund (refund_id),
                  CONSTRAINT fk_fl_store FOREIGN KEY (store_id) REFERENCES stores(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                  COMMENT='财务分录账本'
                """
            )
        )

    # --- point_ledger ---
    if not _table_exists(conn, "point_ledger"):
        conn.execute(
            text(
                """
                CREATE TABLE point_ledger (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
                  customer_id BIGINT UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
                  order_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联订单',
                  refund_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联退款单',
                  delta INT NOT NULL COMMENT '积分变动（可正可负）',
                  balance_after INT NOT NULL COMMENT '变动后余额',
                  entry_type VARCHAR(30) NOT NULL COMMENT '变动类型：EARN/REFUND_DEDUCT/ADJUST 等',
                  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '记录时间',
                  INDEX idx_pl_customer_created (customer_id, created_at),
                  INDEX idx_pl_order (order_id),
                  INDEX idx_pl_refund (refund_id),
                  CONSTRAINT fk_pl_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                  COMMENT='会员积分流水'
                """
            )
        )


def downgrade() -> None:
    conn = op.get_bind()

    if _table_exists(conn, "point_ledger"):
        conn.execute(text("DROP TABLE point_ledger"))
    if _table_exists(conn, "financial_ledger"):
        conn.execute(text("DROP TABLE financial_ledger"))

    if _table_exists(conn, "payment_refunds"):
        if _index_exists(conn, "payment_refunds", "idx_refunds_retry"):
            conn.execute(text("ALTER TABLE payment_refunds DROP INDEX idx_refunds_retry"))
        if _index_exists(conn, "payment_refunds", "uk_refund_idempotency"):
            conn.execute(text("ALTER TABLE payment_refunds DROP INDEX uk_refund_idempotency"))
        for col in ("idempotency_key", "last_error", "next_retry_at", "retry_count"):
            if _column_exists(conn, "payment_refunds", col):
                conn.execute(text(f"ALTER TABLE payment_refunds DROP COLUMN `{col}`"))

    if _table_exists(conn, "webhook_inbox"):
        if _index_exists(conn, "webhook_inbox", "idx_wi_locked"):
            conn.execute(text("ALTER TABLE webhook_inbox DROP INDEX idx_wi_locked"))
        if _index_exists(conn, "webhook_inbox", "idx_wi_retry"):
            conn.execute(text("ALTER TABLE webhook_inbox DROP INDEX idx_wi_retry"))
        for col in ("locked_by", "locked_at", "next_retry_at", "last_error", "attempt_count"):
            if _column_exists(conn, "webhook_inbox", col):
                conn.execute(text(f"ALTER TABLE webhook_inbox DROP COLUMN `{col}`"))

    if _table_exists(conn, "orders"):
        if _fk_exists(conn, "orders", "fk_orders_operator"):
            conn.execute(text("ALTER TABLE orders DROP FOREIGN KEY fk_orders_operator"))
        if _index_exists(conn, "orders", "idx_orders_operator"):
            conn.execute(text("ALTER TABLE orders DROP INDEX idx_orders_operator"))
        if _column_exists(conn, "orders", "operator_id"):
            conn.execute(text("ALTER TABLE orders DROP COLUMN operator_id"))
        conn.execute(
            text(
                "ALTER TABLE orders MODIFY COLUMN idempotency_key VARCHAR(64) NULL "
                "COMMENT '下单幂等键，防重复提交'"
            )
        )

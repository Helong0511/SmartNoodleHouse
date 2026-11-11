"""审计遗留：成本价、临时授权、支付对账、订单冷库。

Revision ID: 0036_audit_remaining_features
Revises: 0035_table_party_shared_cart
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0036_audit_remaining_features"
down_revision = "0035_table_party_shared_cart"
branch_labels = None
depends_on = None


def _table_exists(conn, table: str) -> bool:
    if conn.dialect.name == "sqlite":
        row = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).scalar()
        return bool(row)
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
        ),
        {"t": table},
    ).scalar()
    return bool(row)


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


def upgrade() -> None:
    conn = op.get_bind()
    # products is a required canonical table: without it this revision cannot
    # add cost_price. Fail with an actionable message instead of an opaque
    # ALTER TABLE/OperationalError and do not let Alembic advance.
    required_parent_tables = ("products", "users")
    missing_parent_tables = [
        table for table in required_parent_tables if not _table_exists(conn, table)
    ]
    if missing_parent_tables:
        missing = ", ".join(missing_parent_tables)
        raise RuntimeError(
            "Cannot apply 0036_audit_remaining_features: required canonical "
            f"parent table(s) missing: {missing}. Restore the canonical baseline before retrying."
        )
    if not _column_exists(conn, "products", "cost_price"):
        if conn.dialect.name == "sqlite":
            conn.execute(text("ALTER TABLE products ADD COLUMN cost_price NUMERIC(10,2)"))
        else:
            conn.execute(
                text(
                    "ALTER TABLE products ADD COLUMN cost_price decimal(10,2) NULL "
                    "COMMENT '成本价' AFTER original_price"
                )
            )

    if not _table_exists(conn, "role_delegations"):
        if conn.dialect.name == "sqlite":
            conn.execute(
                text(
                    """
                    CREATE TABLE role_delegations (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      grantor_user_id BIGINT NOT NULL,
                      delegatee_user_id BIGINT NOT NULL,
                      permission_codes JSON NOT NULL,
                      starts_at DATETIME NOT NULL,
                      ends_at DATETIME NOT NULL,
                      status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
                      reason VARCHAR(255),
                      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        else:
            conn.execute(
                text(
                    """
                    CREATE TABLE role_delegations (
                      id bigint UNSIGNED NOT NULL AUTO_INCREMENT,
                      grantor_user_id bigint UNSIGNED NOT NULL,
                      delegatee_user_id bigint UNSIGNED NOT NULL,
                      permission_codes json NOT NULL,
                      starts_at datetime(3) NOT NULL,
                      ends_at datetime(3) NOT NULL,
                      status varchar(20) NOT NULL DEFAULT 'ACTIVE',
                      reason varchar(255) NULL,
                      created_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                      updated_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
                      PRIMARY KEY (id),
                      INDEX idx_rd_delegatee_status_ends (delegatee_user_id, status, ends_at),
                      CONSTRAINT fk_rd_grantor FOREIGN KEY (grantor_user_id) REFERENCES users(id) ON DELETE CASCADE,
                      CONSTRAINT fk_rd_delegatee FOREIGN KEY (delegatee_user_id) REFERENCES users(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )

    if conn.dialect.name == "sqlite":
        if not _table_exists(conn, "payment_reconcile_batches"):
            conn.execute(
                text(
                    """
                    CREATE TABLE payment_reconcile_batches (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      channel VARCHAR(20) NOT NULL,
                      bill_date DATE NOT NULL,
                      status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                      local_count INTEGER NOT NULL DEFAULT 0,
                      channel_count INTEGER NOT NULL DEFAULT 0,
                      mismatch_count INTEGER NOT NULL DEFAULT 0,
                      source_filename VARCHAR(255),
                      error_message VARCHAR(500),
                      created_by BIGINT,
                      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                      UNIQUE(channel, bill_date)
                    )
                    """
                )
            )
        if not _table_exists(conn, "payment_reconcile_diffs"):
            conn.execute(
                text(
                    """
                    CREATE TABLE payment_reconcile_diffs (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      batch_id INTEGER NOT NULL,
                      diff_type VARCHAR(32) NOT NULL,
                      local_payment_no VARCHAR(64),
                      channel_trade_no VARCHAR(128),
                      local_amount NUMERIC(12,2),
                      channel_amount NUMERIC(12,2),
                      detail JSON,
                      status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
                      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
    else:
        if not _table_exists(conn, "payment_reconcile_batches"):
            conn.execute(
                text(
                    """
                    CREATE TABLE payment_reconcile_batches (
                      id bigint UNSIGNED NOT NULL AUTO_INCREMENT,
                      channel varchar(20) NOT NULL,
                      bill_date date NOT NULL,
                      status varchar(20) NOT NULL DEFAULT 'PENDING',
                      local_count int NOT NULL DEFAULT 0,
                      channel_count int NOT NULL DEFAULT 0,
                      mismatch_count int NOT NULL DEFAULT 0,
                      source_filename varchar(255) NULL,
                      error_message varchar(500) NULL,
                      created_by bigint UNSIGNED NULL,
                      created_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                      updated_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
                      PRIMARY KEY (id),
                      UNIQUE KEY uk_prb_channel_bill (channel, bill_date)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )
        if not _table_exists(conn, "payment_reconcile_diffs"):
            conn.execute(
                text(
                    """
                    CREATE TABLE payment_reconcile_diffs (
                      id bigint UNSIGNED NOT NULL AUTO_INCREMENT,
                      batch_id bigint UNSIGNED NOT NULL,
                      diff_type varchar(32) NOT NULL,
                      local_payment_no varchar(64) NULL,
                      channel_trade_no varchar(128) NULL,
                      local_amount decimal(12,2) NULL,
                      channel_amount decimal(12,2) NULL,
                      detail json NULL,
                      status varchar(20) NOT NULL DEFAULT 'OPEN',
                      created_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                      updated_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
                      PRIMARY KEY (id),
                      KEY idx_prd_batch_type (batch_id, diff_type),
                      CONSTRAINT fk_prd_batch FOREIGN KEY (batch_id)
                        REFERENCES payment_reconcile_batches(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )

    if not _table_exists(conn, "archived_order_snapshots"):
        if conn.dialect.name == "sqlite":
            conn.execute(
                text(
                    """
                    CREATE TABLE archived_order_snapshots (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      order_id BIGINT NOT NULL UNIQUE,
                      store_id BIGINT NOT NULL,
                      order_no VARCHAR(64) NOT NULL UNIQUE,
                      status VARCHAR(32) NOT NULL,
                      payment_status VARCHAR(32),
                      original_created_at DATETIME NOT NULL,
                      payload JSON NOT NULL,
                      archived_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        else:
            conn.execute(
                text(
                    """
                    CREATE TABLE archived_order_snapshots (
                      id bigint UNSIGNED NOT NULL AUTO_INCREMENT,
                      order_id bigint UNSIGNED NOT NULL,
                      store_id bigint UNSIGNED NOT NULL,
                      order_no varchar(64) NOT NULL,
                      status varchar(32) NOT NULL,
                      payment_status varchar(32) NULL,
                      original_created_at datetime(3) NOT NULL,
                      payload json NOT NULL,
                      archived_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                      PRIMARY KEY (id),
                      UNIQUE KEY uk_aos_order_id (order_id),
                      UNIQUE KEY uk_aos_order_no (order_no),
                      KEY idx_aos_store_created (store_id, original_created_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )


def downgrade() -> None:
    conn = op.get_bind()
    for table in (
        "archived_order_snapshots",
        "payment_reconcile_diffs",
        "payment_reconcile_batches",
        "role_delegations",
    ):
        if _table_exists(conn, table):
            conn.execute(text(f"DROP TABLE {table}"))

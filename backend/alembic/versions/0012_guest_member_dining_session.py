"""游客/会员状态拆分 + dining_sessions

Revision ID: 0012_guest_member_dining_session
Revises: 0011_store_geo_option_scope
Create Date: 2026-09-10
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0012_guest_member_dining_session"
down_revision = "0011_store_geo_option_scope"
branch_labels = None
depends_on = None


def _col_exists(conn, table: str, col: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = :t AND column_name = :c
            """
        ),
        {"t": table, "c": col},
    ).scalar()
    return int(row or 0) > 0


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


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = :t
            """
        ),
        {"t": table},
    ).scalar()
    return int(row or 0) > 0


def upgrade() -> None:
    conn = op.get_bind()

    cols = [
        ("member_status", "VARCHAR(20) NOT NULL DEFAULT 'GUEST' COMMENT 'GUEST=游客 MEMBER=已主动开通会员' AFTER status"),
        ("account_status", "VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/DISABLED/DELETED' AFTER member_status"),
        ("profile_status", "VARCHAR(20) NOT NULL DEFAULT 'EMPTY' COMMENT 'EMPTY/PARTIAL/COMPLETED' AFTER account_status"),
        ("phone_verified", "TINYINT NOT NULL DEFAULT 0 COMMENT '手机号是否已验证' AFTER profile_status"),
        ("member_activated_at", "DATETIME(3) NULL COMMENT '首次主动开通会员时间' AFTER phone_verified"),
        ("first_seen_at", "DATETIME(3) NULL COMMENT '首次扫码/识别时间' AFTER member_activated_at"),
        ("first_seen_store_id", "BIGINT UNSIGNED NULL COMMENT '首次扫码门店' AFTER first_seen_at"),
        ("first_seen_table_id", "BIGINT UNSIGNED NULL COMMENT '首次扫码桌台' AFTER first_seen_store_id"),
    ]
    for name, ddl in cols:
        if not _col_exists(conn, "customers", name):
            conn.execute(text(f"ALTER TABLE customers ADD COLUMN {name} {ddl}"))

    if not _index_exists(conn, "customers", "idx_customers_member_status"):
        conn.execute(text("ALTER TABLE customers ADD INDEX idx_customers_member_status (member_status)"))

    conn.execute(
        text(
            """
            UPDATE customers c
            SET
              c.member_status = 'MEMBER',
              c.member_activated_at = COALESCE(c.member_activated_at, c.created_at, CURRENT_TIMESTAMP(3)),
              c.account_status = CASE WHEN c.status = 1 THEN 'ACTIVE' WHEN c.status = 0 THEN 'DISABLED' ELSE c.account_status END
            WHERE c.member_status = 'GUEST'
              AND (
                c.member_level_id IS NOT NULL
                OR EXISTS (
                  SELECT 1 FROM customer_wechat_accounts w
                  WHERE w.customer_id = c.id AND w.profile_completed = 1
                )
                OR EXISTS (
                  SELECT 1 FROM customer_alipay_accounts a
                  WHERE a.customer_id = c.id AND a.profile_completed = 1
                )
              )
            """
        )
    )
    conn.execute(
        text(
            """
            UPDATE customers
            SET account_status = CASE WHEN status = 1 THEN 'ACTIVE' ELSE 'DISABLED' END
            WHERE account_status IS NULL OR account_status = ''
            """
        )
    )
    conn.execute(
        text(
            """
            UPDATE customers
            SET profile_status = CASE
              WHEN (nickname IS NOT NULL AND nickname <> '' AND phone IS NOT NULL AND phone <> '') THEN 'COMPLETED'
              WHEN (nickname IS NOT NULL AND nickname <> '')
                OR (phone IS NOT NULL AND phone <> '')
                OR (avatar_url IS NOT NULL AND avatar_url <> '') THEN 'PARTIAL'
              ELSE 'EMPTY'
            END
            WHERE profile_status IS NULL OR profile_status = 'EMPTY'
            """
        )
    )
    conn.execute(
        text(
            """
            UPDATE customers
            SET first_seen_at = COALESCE(first_seen_at, created_at),
                first_seen_store_id = COALESCE(first_seen_store_id, registered_store_id),
                first_seen_table_id = COALESCE(first_seen_table_id, registered_table_id)
            WHERE first_seen_at IS NULL
            """
        )
    )
    conn.execute(
        text(
            """
            UPDATE customers
            SET phone_verified = 1
            WHERE phone IS NOT NULL AND phone <> '' AND phone_verified = 0
            """
        )
    )

    if not _table_exists(conn, "dining_sessions"):
        conn.execute(
            text(
                """
                CREATE TABLE dining_sessions (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  customer_id BIGINT UNSIGNED NOT NULL,
                  store_id BIGINT UNSIGNED NOT NULL,
                  table_id BIGINT UNSIGNED NOT NULL,
                  table_token VARCHAR(64) DEFAULT NULL,
                  status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
                  started_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                  last_active_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                  expires_at DATETIME(3) NOT NULL,
                  ended_at DATETIME(3) DEFAULT NULL,
                  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
                  INDEX idx_ds_customer_status (customer_id, status, expires_at),
                  INDEX idx_ds_store_table (store_id, table_id, status),
                  CONSTRAINT fk_ds_customer FOREIGN KEY (customer_id) REFERENCES customers(id),
                  CONSTRAINT fk_ds_store FOREIGN KEY (store_id) REFERENCES stores(id),
                  CONSTRAINT fk_ds_table FOREIGN KEY (table_id) REFERENCES tables(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )

    if not _col_exists(conn, "orders", "dining_session_id"):
        conn.execute(
            text(
                "ALTER TABLE orders ADD COLUMN dining_session_id BIGINT UNSIGNED NULL "
                "COMMENT '就餐会话 → dining_sessions.id' AFTER table_id"
            )
        )
    if not _index_exists(conn, "orders", "idx_orders_dining_session"):
        conn.execute(text("ALTER TABLE orders ADD INDEX idx_orders_dining_session (dining_session_id)"))


def downgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, "orders", "idx_orders_dining_session"):
        conn.execute(text("ALTER TABLE orders DROP INDEX idx_orders_dining_session"))
    if _col_exists(conn, "orders", "dining_session_id"):
        conn.execute(text("ALTER TABLE orders DROP COLUMN dining_session_id"))
    if _table_exists(conn, "dining_sessions"):
        conn.execute(text("DROP TABLE dining_sessions"))
    if _index_exists(conn, "customers", "idx_customers_member_status"):
        conn.execute(text("ALTER TABLE customers DROP INDEX idx_customers_member_status"))
    for col in (
        "first_seen_table_id",
        "first_seen_store_id",
        "first_seen_at",
        "member_activated_at",
        "phone_verified",
        "profile_status",
        "account_status",
        "member_status",
    ):
        if _col_exists(conn, "customers", col):
            conn.execute(text(f"ALTER TABLE customers DROP COLUMN {col}"))

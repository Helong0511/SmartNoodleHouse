"""统一稳定身份 + 修复不可信手机号验证

Revision ID: 0013_identity_phone_security
Revises: 0012_guest_member_dining_session
Create Date: 2026-09-11
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0013_identity_phone_security"
down_revision = "0012_guest_member_dining_session"
branch_labels = None
depends_on = None


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = DATABASE() AND table_name = :table
                """
            ),
            {"table": table},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "customer_identities"):
        conn.execute(
            text(
                """
                CREATE TABLE customer_identities (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  customer_id BIGINT UNSIGNED NOT NULL,
                  provider VARCHAR(32) NOT NULL,
                  provider_subject VARCHAR(128) NOT NULL,
                  verified TINYINT NOT NULL DEFAULT 1,
                  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                    ON UPDATE CURRENT_TIMESTAMP(3),
                  UNIQUE KEY uk_customer_identity_provider_subject (provider, provider_subject),
                  INDEX idx_customer_identity_customer (customer_id, provider),
                  CONSTRAINT fk_customer_identity_customer
                    FOREIGN KEY (customer_id) REFERENCES customers(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )

    conn.execute(
        text(
            """
            INSERT IGNORE INTO customer_identities
              (customer_id, provider, provider_subject, verified)
            SELECT customer_id, 'WECHAT_OPENID', openid, 1
            FROM customer_wechat_accounts
            WHERE openid IS NOT NULL AND openid <> ''
            """
        )
    )
    # 仅当同一 UnionID 的历史记录本来就指向同一 Customer 时回填，拒绝静默合并冲突数据。
    conn.execute(
        text(
            """
            INSERT IGNORE INTO customer_identities
              (customer_id, provider, provider_subject, verified)
            SELECT MIN(customer_id), 'WECHAT_UNIONID', unionid, 1
            FROM customer_wechat_accounts
            WHERE unionid IS NOT NULL AND unionid <> ''
            GROUP BY unionid
            HAVING COUNT(DISTINCT customer_id) = 1
            """
        )
    )
    conn.execute(
        text(
            """
            INSERT IGNORE INTO customer_identities
              (customer_id, provider, provider_subject, verified)
            SELECT customer_id, 'ALIPAY_USER_ID', alipay_user_id, 1
            FROM customer_alipay_accounts
            WHERE alipay_user_id IS NOT NULL AND alipay_user_id <> ''
            """
        )
    )
    # 0012 旧版本曾把所有非空手机号误标为已验证。没有 PHONE 身份凭据的全部降为未验证。
    conn.execute(
        text(
            """
            UPDATE customers c
            SET c.phone_verified = 0
            WHERE c.phone_verified = 1
              AND NOT EXISTS (
                SELECT 1 FROM customer_identities i
                WHERE i.customer_id = c.id
                  AND i.provider = 'PHONE'
                  AND i.provider_subject = c.phone
                  AND i.verified = 1
              )
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "customer_identities"):
        conn.execute(text("DROP TABLE customer_identities"))

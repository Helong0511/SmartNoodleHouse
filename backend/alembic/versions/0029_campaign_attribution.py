"""营销触达日志与活动归因表。

Revision ID: 0029_campaign_attribution
Revises: 0028_marketing_segments
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0029_campaign_attribution"
down_revision = "0028_marketing_segments"
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


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        if not _table_exists(conn, "marketing_message_logs"):
            conn.execute(
                text(
                    """
                    CREATE TABLE marketing_message_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        store_id INTEGER,
                        campaign_id INTEGER,
                        customer_id INTEGER NOT NULL,
                        channel VARCHAR(20) NOT NULL DEFAULT 'WECHAT',
                        template_key VARCHAR(64) NOT NULL,
                        template_id VARCHAR(120),
                        payload_json JSON,
                        status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                        error_message VARCHAR(500),
                        idempotency_key VARCHAR(128) NOT NULL UNIQUE,
                        sent_at DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        if not _table_exists(conn, "campaign_attributions"):
            conn.execute(
                text(
                    """
                    CREATE TABLE campaign_attributions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        campaign_id INTEGER NOT NULL,
                        customer_id INTEGER NOT NULL,
                        store_id INTEGER,
                        order_id INTEGER NOT NULL,
                        attribution_type VARCHAR(20) NOT NULL DEFAULT 'COUPON',
                        coupon_id INTEGER,
                        gross_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
                        subsidy_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
                        net_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
                        status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
                        idempotency_key VARCHAR(128) NOT NULL UNIQUE,
                        meta JSON,
                        attributed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        reversed_at DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        return

    if not _table_exists(conn, "marketing_message_logs"):
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS `marketing_message_logs` (
                  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
                  `store_id` bigint UNSIGNED NULL,
                  `campaign_id` bigint UNSIGNED NULL,
                  `customer_id` bigint UNSIGNED NOT NULL,
                  `channel` varchar(20) NOT NULL DEFAULT 'WECHAT',
                  `template_key` varchar(64) NOT NULL,
                  `template_id` varchar(120) NULL,
                  `payload_json` json NULL,
                  `status` varchar(20) NOT NULL DEFAULT 'PENDING',
                  `error_message` varchar(500) NULL,
                  `idempotency_key` varchar(128) NOT NULL,
                  `sent_at` datetime(3) NULL,
                  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
                  PRIMARY KEY (`id`) USING BTREE,
                  UNIQUE INDEX `uk_mml_idem`(`idempotency_key` ASC) USING BTREE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )
    if not _table_exists(conn, "campaign_attributions"):
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS `campaign_attributions` (
                  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
                  `campaign_id` bigint UNSIGNED NOT NULL,
                  `customer_id` bigint UNSIGNED NOT NULL,
                  `store_id` bigint UNSIGNED NULL,
                  `order_id` bigint UNSIGNED NOT NULL,
                  `attribution_type` varchar(20) NOT NULL DEFAULT 'COUPON',
                  `coupon_id` bigint UNSIGNED NULL,
                  `gross_amount` decimal(12,2) NOT NULL DEFAULT 0.00,
                  `subsidy_amount` decimal(12,2) NOT NULL DEFAULT 0.00,
                  `net_amount` decimal(12,2) NOT NULL DEFAULT 0.00,
                  `status` varchar(20) NOT NULL DEFAULT 'ACTIVE',
                  `idempotency_key` varchar(128) NOT NULL,
                  `meta` json NULL,
                  `attributed_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                  `reversed_at` datetime(3) NULL,
                  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
                  PRIMARY KEY (`id`) USING BTREE,
                  UNIQUE INDEX `uk_ca_idem`(`idempotency_key` ASC) USING BTREE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    for t in ("campaign_attributions", "marketing_message_logs"):
        if _table_exists(conn, t):
            if conn.dialect.name == "sqlite":
                conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
            else:
                conn.execute(text(f"DROP TABLE IF EXISTS `{t}`"))

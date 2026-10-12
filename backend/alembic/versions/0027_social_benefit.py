"""社会贡献公益四表。

Revision ID: 0027_social_benefit
Revises: 0026_birthday_calendar
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0027_social_benefit"
down_revision = "0026_birthday_calendar"
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
        if not _table_exists(conn, "social_identity_types"):
            conn.execute(
                text(
                    """
                    CREATE TABLE social_identity_types (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        code VARCHAR(32) NOT NULL UNIQUE,
                        name VARCHAR(50) NOT NULL,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        status INTEGER NOT NULL DEFAULT 1,
                        default_verify_hints JSON,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        if not _table_exists(conn, "customer_social_identities"):
            conn.execute(
                text(
                    """
                    CREATE TABLE customer_social_identities (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        customer_id INTEGER NOT NULL,
                        identity_code VARCHAR(32) NOT NULL,
                        store_id INTEGER,
                        status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                        verify_method VARCHAR(50),
                        credential_note VARCHAR(255),
                        credential_media_url VARCHAR(500),
                        valid_from DATETIME,
                        valid_to DATETIME,
                        reviewed_by INTEGER,
                        reviewed_at DATETIME,
                        reject_reason VARCHAR(255),
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        if not _table_exists(conn, "social_benefit_rules"):
            conn.execute(
                text(
                    """
                    CREATE TABLE social_benefit_rules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        store_id INTEGER,
                        identity_code VARCHAR(32) NOT NULL,
                        name VARCHAR(100) NOT NULL,
                        time_windows JSON,
                        weekdays JSON,
                        date_from DATE,
                        date_to DATE,
                        benefit_type VARCHAR(30) NOT NULL DEFAULT 'ORDER_DISCOUNT',
                        discount_rate NUMERIC(5,2),
                        coupon_template_id INTEGER,
                        gift_product_id INTEGER,
                        applicable_category_ids JSON,
                        applicable_product_ids JSON,
                        daily_limit INTEGER NOT NULL DEFAULT 1,
                        monthly_budget NUMERIC(12,2),
                        budget_action VARCHAR(10) NOT NULL DEFAULT 'WARN',
                        stack_policy VARCHAR(30) NOT NULL DEFAULT 'EXCLUSIVE',
                        max_subsidy_amount NUMERIC(10,2),
                        priority INTEGER NOT NULL DEFAULT 100,
                        status INTEGER NOT NULL DEFAULT 1,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        if not _table_exists(conn, "social_benefit_redemptions"):
            conn.execute(
                text(
                    """
                    CREATE TABLE social_benefit_redemptions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        customer_id INTEGER NOT NULL,
                        store_id INTEGER NOT NULL,
                        order_id INTEGER,
                        identity_code VARCHAR(32) NOT NULL,
                        rule_id INTEGER NOT NULL,
                        benefit_type VARCHAR(30) NOT NULL,
                        subsidy_amount NUMERIC(10,2) NOT NULL DEFAULT 0,
                        coupon_id INTEGER,
                        operator_user_id INTEGER,
                        idempotency_key VARCHAR(128) NOT NULL UNIQUE,
                        status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                        redeemed_at DATETIME,
                        meta JSON,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        return

    # MySQL：与 struct.sql 对齐（省略部分 FK/索引差异由 struct 真源覆盖）
    stmts = [
        """
        CREATE TABLE IF NOT EXISTS `social_identity_types` (
          `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
          `code` varchar(32) NOT NULL,
          `name` varchar(50) NOT NULL,
          `sort_order` int NOT NULL DEFAULT 0,
          `status` tinyint NOT NULL DEFAULT 1,
          `default_verify_hints` json NULL,
          `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (`id`) USING BTREE,
          UNIQUE INDEX `uk_sit_code`(`code` ASC) USING BTREE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS `customer_social_identities` (
          `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
          `customer_id` bigint UNSIGNED NOT NULL,
          `identity_code` varchar(32) NOT NULL,
          `store_id` bigint UNSIGNED NULL,
          `status` varchar(20) NOT NULL DEFAULT 'PENDING',
          `verify_method` varchar(50) NULL,
          `credential_note` varchar(255) NULL,
          `credential_media_url` varchar(500) NULL,
          `valid_from` datetime(3) NULL,
          `valid_to` datetime(3) NULL,
          `reviewed_by` bigint UNSIGNED NULL,
          `reviewed_at` datetime(3) NULL,
          `reject_reason` varchar(255) NULL,
          `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (`id`) USING BTREE,
          INDEX `idx_csi_customer_status`(`customer_id` ASC, `status` ASC) USING BTREE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS `social_benefit_rules` (
          `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
          `store_id` bigint UNSIGNED NULL,
          `identity_code` varchar(32) NOT NULL,
          `name` varchar(100) NOT NULL,
          `time_windows` json NULL,
          `weekdays` json NULL,
          `date_from` date NULL,
          `date_to` date NULL,
          `benefit_type` varchar(30) NOT NULL DEFAULT 'ORDER_DISCOUNT',
          `discount_rate` decimal(5,2) NULL,
          `coupon_template_id` bigint UNSIGNED NULL,
          `gift_product_id` bigint UNSIGNED NULL,
          `applicable_category_ids` json NULL,
          `applicable_product_ids` json NULL,
          `daily_limit` int NOT NULL DEFAULT 1,
          `monthly_budget` decimal(12,2) NULL,
          `budget_action` varchar(10) NOT NULL DEFAULT 'WARN',
          `stack_policy` varchar(30) NOT NULL DEFAULT 'EXCLUSIVE',
          `max_subsidy_amount` decimal(10,2) NULL,
          `priority` int NOT NULL DEFAULT 100,
          `status` tinyint NOT NULL DEFAULT 1,
          `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (`id`) USING BTREE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS `social_benefit_redemptions` (
          `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
          `customer_id` bigint UNSIGNED NOT NULL,
          `store_id` bigint UNSIGNED NOT NULL,
          `order_id` bigint UNSIGNED NULL,
          `identity_code` varchar(32) NOT NULL,
          `rule_id` bigint UNSIGNED NOT NULL,
          `benefit_type` varchar(30) NOT NULL,
          `subsidy_amount` decimal(10,2) NOT NULL DEFAULT 0.00,
          `coupon_id` bigint UNSIGNED NULL,
          `operator_user_id` bigint UNSIGNED NULL,
          `idempotency_key` varchar(128) NOT NULL,
          `status` varchar(20) NOT NULL DEFAULT 'PENDING',
          `redeemed_at` datetime(3) NULL,
          `meta` json NULL,
          `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (`id`) USING BTREE,
          UNIQUE INDEX `uk_sbrd_idem`(`idempotency_key` ASC) USING BTREE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ]
    for s in stmts:
        # 表已存在则跳过
        name = "social_identity_types"
        if "customer_social_identities" in s:
            name = "customer_social_identities"
        elif "social_benefit_rules" in s and "redemptions" not in s:
            name = "social_benefit_rules"
        elif "social_benefit_redemptions" in s:
            name = "social_benefit_redemptions"
        if _table_exists(conn, name):
            continue
        conn.execute(text(s))


def downgrade() -> None:
    conn = op.get_bind()
    for t in (
        "social_benefit_redemptions",
        "social_benefit_rules",
        "customer_social_identities",
        "social_identity_types",
    ):
        if _table_exists(conn, t):
            conn.execute(text(f"DROP TABLE IF EXISTS `{t}`" if conn.dialect.name != "sqlite" else f"DROP TABLE IF EXISTS {t}"))

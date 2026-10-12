"""营销分群与活动任务表。

Revision ID: 0028_marketing_segments
Revises: 0027_social_benefit
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0028_marketing_segments"
down_revision = "0027_social_benefit"
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
        tables = {
            "customer_segments": """
                CREATE TABLE customer_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id INTEGER,
                    code VARCHAR(64) NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    segment_type VARCHAR(20) NOT NULL DEFAULT 'DYNAMIC',
                    rules_json JSON,
                    status INTEGER NOT NULL DEFAULT 1,
                    estimated_count INTEGER,
                    materialized_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """,
            "customer_segment_members": """
                CREATE TABLE customer_segment_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    segment_id INTEGER NOT NULL,
                    customer_id INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(segment_id, customer_id)
                )
            """,
            "marketing_campaigns": """
                CREATE TABLE marketing_campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id INTEGER,
                    code VARCHAR(64) NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    campaign_type VARCHAR(30) NOT NULL DEFAULT 'MEMBER_DAY',
                    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
                    segment_id INTEGER,
                    coupon_template_id INTEGER,
                    start_at DATETIME,
                    end_at DATETIME,
                    budget_cap NUMERIC(12,2),
                    weekly_cap_override INTEGER,
                    boost_product_ids JSON,
                    remark VARCHAR(500),
                    published_at DATETIME,
                    created_by INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """,
            "marketing_rules": """
                CREATE TABLE marketing_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER NOT NULL,
                    rule_type VARCHAR(30) NOT NULL DEFAULT 'SEGMENT',
                    config_json JSON,
                    priority INTEGER NOT NULL DEFAULT 100,
                    status INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """,
            "marketing_tasks": """
                CREATE TABLE marketing_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER NOT NULL,
                    customer_id INTEGER NOT NULL,
                    store_id INTEGER,
                    action_type VARCHAR(30) NOT NULL DEFAULT 'ISSUE_COUPON',
                    payload_json JSON,
                    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
                    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                    try_count INTEGER NOT NULL DEFAULT 0,
                    last_error VARCHAR(500),
                    result_json JSON,
                    scheduled_at DATETIME,
                    executed_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """,
        }
        for name, ddl in tables.items():
            if not _table_exists(conn, name):
                conn.execute(text(ddl))
        return

    stmts = [
        (
            "customer_segments",
            """
            CREATE TABLE IF NOT EXISTS `customer_segments` (
              `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
              `store_id` bigint UNSIGNED NULL DEFAULT NULL,
              `code` varchar(64) NOT NULL,
              `name` varchar(100) NOT NULL,
              `segment_type` varchar(20) NOT NULL DEFAULT 'DYNAMIC',
              `rules_json` json NULL,
              `status` tinyint NOT NULL DEFAULT 1,
              `estimated_count` int NULL DEFAULT NULL,
              `materialized_at` datetime(3) NULL DEFAULT NULL,
              `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
              `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
              PRIMARY KEY (`id`) USING BTREE,
              UNIQUE INDEX `uk_cseg_store_code`(`store_id` ASC, `code` ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
        ),
        (
            "customer_segment_members",
            """
            CREATE TABLE IF NOT EXISTS `customer_segment_members` (
              `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
              `segment_id` bigint UNSIGNED NOT NULL,
              `customer_id` bigint UNSIGNED NOT NULL,
              `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
              PRIMARY KEY (`id`) USING BTREE,
              UNIQUE INDEX `uk_csm_seg_customer`(`segment_id` ASC, `customer_id` ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
        ),
        (
            "marketing_campaigns",
            """
            CREATE TABLE IF NOT EXISTS `marketing_campaigns` (
              `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
              `store_id` bigint UNSIGNED NULL DEFAULT NULL,
              `code` varchar(64) NOT NULL,
              `name` varchar(100) NOT NULL,
              `campaign_type` varchar(30) NOT NULL DEFAULT 'MEMBER_DAY',
              `status` varchar(20) NOT NULL DEFAULT 'DRAFT',
              `segment_id` bigint UNSIGNED NULL DEFAULT NULL,
              `coupon_template_id` bigint UNSIGNED NULL DEFAULT NULL,
              `start_at` datetime(3) NULL DEFAULT NULL,
              `end_at` datetime(3) NULL DEFAULT NULL,
              `budget_cap` decimal(12,2) NULL DEFAULT NULL,
              `weekly_cap_override` int NULL DEFAULT NULL,
              `boost_product_ids` json NULL,
              `remark` varchar(500) NULL DEFAULT NULL,
              `published_at` datetime(3) NULL DEFAULT NULL,
              `created_by` bigint UNSIGNED NULL DEFAULT NULL,
              `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
              `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
              PRIMARY KEY (`id`) USING BTREE,
              UNIQUE INDEX `uk_mc_store_code`(`store_id` ASC, `code` ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
        ),
        (
            "marketing_rules",
            """
            CREATE TABLE IF NOT EXISTS `marketing_rules` (
              `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
              `campaign_id` bigint UNSIGNED NOT NULL,
              `rule_type` varchar(30) NOT NULL DEFAULT 'SEGMENT',
              `config_json` json NULL,
              `priority` int NOT NULL DEFAULT 100,
              `status` tinyint NOT NULL DEFAULT 1,
              `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
              `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
              PRIMARY KEY (`id`) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
        ),
        (
            "marketing_tasks",
            """
            CREATE TABLE IF NOT EXISTS `marketing_tasks` (
              `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
              `campaign_id` bigint UNSIGNED NOT NULL,
              `customer_id` bigint UNSIGNED NOT NULL,
              `store_id` bigint UNSIGNED NULL DEFAULT NULL,
              `action_type` varchar(30) NOT NULL DEFAULT 'ISSUE_COUPON',
              `payload_json` json NULL,
              `idempotency_key` varchar(128) NOT NULL,
              `status` varchar(20) NOT NULL DEFAULT 'PENDING',
              `try_count` int NOT NULL DEFAULT 0,
              `last_error` varchar(500) NULL DEFAULT NULL,
              `result_json` json NULL,
              `scheduled_at` datetime(3) NULL DEFAULT NULL,
              `executed_at` datetime(3) NULL DEFAULT NULL,
              `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
              `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
              PRIMARY KEY (`id`) USING BTREE,
              UNIQUE INDEX `uk_mt_idem`(`idempotency_key` ASC) USING BTREE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
        ),
    ]
    for name, ddl in stmts:
        if not _table_exists(conn, name):
            conn.execute(text(ddl))


def downgrade() -> None:
    conn = op.get_bind()
    for t in (
        "marketing_tasks",
        "marketing_rules",
        "marketing_campaigns",
        "customer_segment_members",
        "customer_segments",
    ):
        if _table_exists(conn, t):
            if conn.dialect.name == "sqlite":
                conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
            else:
                conn.execute(text(f"DROP TABLE IF EXISTS `{t}`"))

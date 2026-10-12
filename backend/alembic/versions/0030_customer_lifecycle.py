"""顾客生命周期 / RFM 快照表。

Revision ID: 0030_customer_lifecycle
Revises: 0029_campaign_attribution
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0030_customer_lifecycle"
down_revision = "0029_campaign_attribution"
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
    if _table_exists(conn, "customer_lifecycle"):
        return
    if conn.dialect.name == "sqlite":
        conn.execute(
            text(
                """
                CREATE TABLE customer_lifecycle (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id INTEGER NOT NULL UNIQUE,
                    lifecycle_code VARCHAR(20) NOT NULL DEFAULT 'GUEST',
                    rfm_segment VARCHAR(32),
                    r_score INTEGER,
                    f_score INTEGER,
                    m_score INTEGER,
                    silence_days INTEGER NOT NULL DEFAULT 0,
                    order_count_90d INTEGER NOT NULL DEFAULT 0,
                    pay_amount_90d NUMERIC(12,2) NOT NULL DEFAULT 0,
                    computed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        return
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS `customer_lifecycle` (
              `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
              `customer_id` bigint UNSIGNED NOT NULL,
              `lifecycle_code` varchar(20) NOT NULL DEFAULT 'GUEST',
              `rfm_segment` varchar(32) NULL,
              `r_score` tinyint NULL,
              `f_score` tinyint NULL,
              `m_score` tinyint NULL,
              `silence_days` int NOT NULL DEFAULT 0,
              `order_count_90d` int NOT NULL DEFAULT 0,
              `pay_amount_90d` decimal(12, 2) NOT NULL DEFAULT 0.00,
              `computed_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
              `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
              `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ON UPDATE CURRENT_TIMESTAMP(3),
              PRIMARY KEY (`id`) USING BTREE,
              UNIQUE INDEX `uk_cl_customer`(`customer_id` ASC) USING BTREE,
              INDEX `idx_cl_lifecycle`(`lifecycle_code` ASC) USING BTREE,
              INDEX `idx_cl_rfm`(`rfm_segment` ASC) USING BTREE,
              INDEX `idx_cl_silence`(`silence_days` ASC) USING BTREE,
              CONSTRAINT `fk_cl_customer` FOREIGN KEY (`customer_id`)
                REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "customer_lifecycle"):
        if conn.dialect.name == "sqlite":
            conn.execute(text("DROP TABLE IF EXISTS customer_lifecycle"))
        else:
            conn.execute(text("DROP TABLE IF EXISTS `customer_lifecycle`"))

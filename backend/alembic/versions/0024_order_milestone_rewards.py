"""门店累计成单里程碑发券规则表。

Revision ID: 0024_order_milestone_rewards
Revises: 0023_store_logo
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0024_order_milestone_rewards"
down_revision = "0023_store_logo"
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
    if _table_exists(conn, "order_milestone_rewards"):
        return
    if conn.dialect.name == "sqlite":
        conn.execute(
            text(
                """
                CREATE TABLE order_milestone_rewards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id INTEGER NOT NULL,
                    threshold_orders INTEGER NOT NULL,
                    template_id INTEGER NOT NULL,
                    status INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (store_id, threshold_orders)
                )
                """
            )
        )
        return
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS `order_milestone_rewards` (
              `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
              `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
              `threshold_orders` int NOT NULL COMMENT '累计成功支付订单数阈值',
              `template_id` bigint UNSIGNED NOT NULL COMMENT '券模板 → coupon_templates.id',
              `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
              `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
              `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
              PRIMARY KEY (`id`) USING BTREE,
              UNIQUE INDEX `uk_omr_store_threshold`(`store_id` ASC, `threshold_orders` ASC) USING BTREE,
              INDEX `idx_omr_store_status`(`store_id` ASC, `status` ASC) USING BTREE,
              INDEX `idx_omr_template`(`template_id` ASC) USING BTREE,
              CONSTRAINT `fk_omr_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
              CONSTRAINT `fk_omr_template` FOREIGN KEY (`template_id`) REFERENCES `coupon_templates` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
            ) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci
              COMMENT = '门店累计成单里程碑发券规则' ROW_FORMAT = Dynamic
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "order_milestone_rewards"):
        return
    if conn.dialect.name == "sqlite":
        conn.execute(text("DROP TABLE order_milestone_rewards"))
        return
    conn.execute(text("DROP TABLE IF EXISTS `order_milestone_rewards`"))

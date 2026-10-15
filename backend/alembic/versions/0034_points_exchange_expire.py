"""积分兑换选项表 + 积分过期配置。

Revision ID: 0034_points_exchange_expire
Revises: 0033_birthday_locked
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0034_points_exchange_expire"
down_revision = "0033_birthday_locked"
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
    if _table_exists(conn, "point_exchange_options"):
        return
    if conn.dialect.name == "sqlite":
        conn.execute(
            text(
                """
                CREATE TABLE point_exchange_options (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id INTEGER,
                    name VARCHAR(100) NOT NULL,
                    template_id INTEGER NOT NULL,
                    points_cost INTEGER NOT NULL,
                    status INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE,
                    FOREIGN KEY (template_id) REFERENCES coupon_templates(id) ON DELETE RESTRICT,
                    UNIQUE (store_id, name)
                )
                """
            )
        )
        return
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS `point_exchange_options` (
              `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
              `store_id` bigint UNSIGNED NULL COMMENT '门店 → stores.id；NULL=总部全局选项',
              `name` varchar(100) NOT NULL COMMENT '选项名称',
              `template_id` bigint UNSIGNED NOT NULL COMMENT '券模板 → coupon_templates.id',
              `points_cost` int NOT NULL COMMENT '所需积分',
              `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
              `sort_order` int NOT NULL DEFAULT 0 COMMENT '排序（升序）',
              `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
              `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
              PRIMARY KEY (`id`) USING BTREE,
              UNIQUE INDEX `uk_peo_store_name`(`store_id` ASC, `name` ASC) USING BTREE,
              INDEX `idx_peo_store_status`(`store_id` ASC, `status` ASC, `sort_order` ASC) USING BTREE,
              INDEX `idx_peo_template`(`template_id` ASC) USING BTREE,
              CONSTRAINT `fk_peo_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
              CONSTRAINT `fk_peo_template` FOREIGN KEY (`template_id`) REFERENCES `coupon_templates` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
            ) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci
              COMMENT = '积分兑换选项' ROW_FORMAT = Dynamic
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "point_exchange_options"):
        return
    if conn.dialect.name == "sqlite":
        conn.execute(text("DROP TABLE point_exchange_options"))
        return
    conn.execute(text("DROP TABLE IF EXISTS `point_exchange_options`"))

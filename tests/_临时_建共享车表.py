"""本地调试：幂等创建共享车三表（不改 alembic 历史）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text

from app.core.database import engine

STATEMENTS = [
    """
CREATE TABLE IF NOT EXISTS `table_parties` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店',
  `table_id` bigint UNSIGNED NOT NULL COMMENT '桌台',
  `host_customer_id` bigint UNSIGNED NOT NULL COMMENT '桌主',
  `status` varchar(20) NOT NULL DEFAULT 'OPEN',
  `revision` int NOT NULL DEFAULT 1,
  `expires_at` datetime(3) NOT NULL,
  `closed_at` datetime(3) NULL DEFAULT NULL,
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_tp_store_table_status`(`store_id` ASC, `table_id` ASC, `status` ASC) USING BTREE,
  INDEX `idx_tp_host`(`host_customer_id` ASC) USING BTREE,
  INDEX `fk_tp_table`(`table_id` ASC) USING BTREE,
  CONSTRAINT `fk_tp_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_tp_table` FOREIGN KEY (`table_id`) REFERENCES `tables` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_tp_host` FOREIGN KEY (`host_customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
    """
CREATE TABLE IF NOT EXISTS `table_party_members` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
  `party_id` bigint UNSIGNED NOT NULL,
  `customer_id` bigint UNSIGNED NOT NULL,
  `role` varchar(20) NOT NULL DEFAULT 'MEMBER',
  `nickname_snap` varchar(100) NULL DEFAULT NULL,
  `joined_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `left_at` datetime(3) NULL DEFAULT NULL,
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_tpm_party_customer`(`party_id` ASC, `customer_id` ASC) USING BTREE,
  INDEX `idx_tpm_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_tpm_party` FOREIGN KEY (`party_id`) REFERENCES `table_parties` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_tpm_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
    """
CREATE TABLE IF NOT EXISTS `shared_cart_items` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
  `party_id` bigint UNSIGNED NOT NULL,
  `added_by_customer_id` bigint UNSIGNED NOT NULL,
  `product_id` bigint UNSIGNED NOT NULL,
  `product_name` varchar(100) NOT NULL,
  `quantity` int NOT NULL DEFAULT 1,
  `unit_price` decimal(10,2) NOT NULL,
  `option_ids` json NULL,
  `option_text` varchar(255) NULL DEFAULT NULL,
  `remark` varchar(200) NULL DEFAULT NULL,
  `category_id` bigint UNSIGNED NULL DEFAULT NULL,
  `main_image` varchar(500) NULL DEFAULT NULL,
  `no_member_discount` tinyint NOT NULL DEFAULT 0,
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_sci_party`(`party_id` ASC) USING BTREE,
  INDEX `idx_sci_adder`(`added_by_customer_id` ASC) USING BTREE,
  INDEX `idx_sci_product`(`product_id` ASC) USING BTREE,
  CONSTRAINT `fk_sci_party` FOREIGN KEY (`party_id`) REFERENCES `table_parties` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_sci_adder` FOREIGN KEY (`added_by_customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_sci_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
]


async def main() -> None:
    async with engine.begin() as conn:
        for stmt in STATEMENTS:
            await conn.execute(text(stmt))
        rows = await conn.execute(
            text(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME IN ('table_parties','table_party_members','shared_cart_items') "
                "ORDER BY 1"
            )
        )
        print("ok", [r[0] for r in rows])


if __name__ == "__main__":
    asyncio.run(main())

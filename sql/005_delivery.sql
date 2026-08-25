-- 005: 美团 / 饿了么外卖渠道
USE smart_noodle_house;

-- 平台菜品 ↔ 本系统商品映射
CREATE TABLE IF NOT EXISTS delivery_product_maps (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  store_id BIGINT UNSIGNED NOT NULL,
  platform VARCHAR(20) NOT NULL COMMENT 'MEITUAN/ELEME',
  platform_sku_id VARCHAR(64) NOT NULL COMMENT '平台菜品/SKU ID',
  platform_sku_name VARCHAR(100) DEFAULT NULL,
  product_id BIGINT UNSIGNED NOT NULL,
  option_map_json JSON DEFAULT NULL COMMENT '平台规格码→本系统 option_id 映射',
  status TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_platform_sku (store_id, platform, platform_sku_id),
  INDEX idx_dpm_product (product_id),
  CONSTRAINT fk_dpm_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_dpm_product FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 外卖订单扩展（关联统一 orders）
CREATE TABLE IF NOT EXISTS delivery_orders (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  order_id BIGINT UNSIGNED NOT NULL UNIQUE,
  platform VARCHAR(20) NOT NULL COMMENT 'MEITUAN/ELEME',
  platform_order_id VARCHAR(64) NOT NULL,
  platform_day_seq VARCHAR(32) DEFAULT NULL COMMENT '当日流水号/#号牌',
  delivery_status VARCHAR(30) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/ACCEPTED/READY/DELIVERING/DONE/CANCELLED',
  recipient_name VARCHAR(50) DEFAULT NULL,
  recipient_phone VARCHAR(20) DEFAULT NULL,
  recipient_address VARCHAR(500) DEFAULT NULL,
  rider_name VARCHAR(50) DEFAULT NULL,
  rider_phone VARCHAR(20) DEFAULT NULL,
  expected_delivery_at DATETIME(3) DEFAULT NULL,
  platform_fee DECIMAL(10,2) DEFAULT 0.00,
  packing_fee DECIMAL(10,2) DEFAULT 0.00,
  delivery_fee DECIMAL(10,2) DEFAULT 0.00,
  raw_payload JSON DEFAULT NULL,
  last_sync_at DATETIME(3) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_platform_order (platform, platform_order_id),
  INDEX idx_do_status (delivery_status, created_at),
  CONSTRAINT fk_do_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

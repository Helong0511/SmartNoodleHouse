-- 006: 外卖平台深度对接（门店绑定/退款/回调/商品扩展）
-- MySQL 不支持 ADD COLUMN IF NOT EXISTS，改用 information_schema 判断，可重复执行

USE smart_noodle_house;

-- ========== 商品扩展（库存/打包费/起售量） ==========
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'products' AND column_name = 'stock') = 0,
  'ALTER TABLE products ADD COLUMN stock INT NOT NULL DEFAULT 999 COMMENT ''库存，999=不限''',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'products' AND column_name = 'box_price') = 0,
  'ALTER TABLE products ADD COLUMN box_price DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT ''餐盒费''',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'products' AND column_name = 'min_order_count') = 0,
  'ALTER TABLE products ADD COLUMN min_order_count INT NOT NULL DEFAULT 1 COMMENT ''最少购买份数''',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ========== 映射增强 ==========
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'delivery_product_maps' AND column_name = 'platform_spu_code') = 0,
  'ALTER TABLE delivery_product_maps ADD COLUMN platform_spu_code VARCHAR(64) DEFAULT NULL COMMENT ''美团app_food_code/饿了么SPU''',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'delivery_product_maps' AND column_name = 'app_food_code') = 0,
  'ALTER TABLE delivery_product_maps ADD COLUMN app_food_code VARCHAR(64) DEFAULT NULL COMMENT ''本系统对外编码=product_no或组合码''',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'delivery_product_maps' AND column_name = 'option_signature') = 0,
  'ALTER TABLE delivery_product_maps ADD COLUMN option_signature VARCHAR(128) DEFAULT NULL COMMENT ''规格组合签名''',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ========== 外卖订单扩展 ==========
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'delivery_orders' AND column_name = 'logistics_status') = 0,
  'ALTER TABLE delivery_orders ADD COLUMN logistics_status VARCHAR(30) DEFAULT NULL COMMENT ''配送状态''',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'delivery_orders' AND column_name = 'logistics_code') = 0,
  'ALTER TABLE delivery_orders ADD COLUMN logistics_code VARCHAR(50) DEFAULT NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'delivery_orders' AND column_name = 'refund_status') = 0,
  'ALTER TABLE delivery_orders ADD COLUMN refund_status VARCHAR(30) DEFAULT ''NONE'' COMMENT ''NONE/PENDING/APPROVED/REJECTED''',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'delivery_orders' AND column_name = 'urge_count') = 0,
  'ALTER TABLE delivery_orders ADD COLUMN urge_count INT NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'delivery_orders' AND column_name = 'completed_at') = 0,
  'ALTER TABLE delivery_orders ADD COLUMN completed_at DATETIME(3) DEFAULT NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ========== 平台门店授权绑定 ==========
CREATE TABLE IF NOT EXISTS platform_store_bindings (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  store_id BIGINT UNSIGNED NOT NULL,
  platform VARCHAR(20) NOT NULL COMMENT 'MEITUAN/ELEME',
  platform_shop_id VARCHAR(64) NOT NULL COMMENT '美团app_poi_code/饿了么shopId',
  platform_shop_name VARCHAR(100) DEFAULT NULL,
  access_token TEXT DEFAULT NULL,
  refresh_token TEXT DEFAULT NULL,
  token_expires_at DATETIME(3) DEFAULT NULL,
  extra_config JSON DEFAULT NULL,
  status TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_store_platform_shop (store_id, platform, platform_shop_id),
  CONSTRAINT fk_psb_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ========== 外卖退款记录 ==========
CREATE TABLE IF NOT EXISTS delivery_refunds (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  delivery_order_id BIGINT UNSIGNED NOT NULL,
  order_id BIGINT UNSIGNED NOT NULL,
  platform VARCHAR(20) NOT NULL,
  platform_refund_id VARCHAR(64) DEFAULT NULL,
  refund_type VARCHAR(30) NOT NULL DEFAULT 'FULL' COMMENT 'FULL/PARTIAL',
  amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  reason VARCHAR(500) DEFAULT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/APPROVED/REJECTED',
  raw_payload JSON DEFAULT NULL,
  handled_at DATETIME(3) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_dr_order (order_id),
  CONSTRAINT fk_dr_do FOREIGN KEY (delivery_order_id) REFERENCES delivery_orders(id) ON DELETE CASCADE,
  CONSTRAINT fk_dr_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ========== 平台回调审计（催单/配送/退款等） ==========
CREATE TABLE IF NOT EXISTS delivery_callback_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  platform VARCHAR(20) NOT NULL,
  event_type VARCHAR(50) NOT NULL,
  platform_order_id VARCHAR(64) DEFAULT NULL,
  order_id BIGINT UNSIGNED DEFAULT NULL,
  payload JSON DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_dcl_platform_order (platform, platform_order_id, created_at),
  INDEX idx_dcl_event (event_type, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

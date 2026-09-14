-- 004: 持券记录新增发券人 + 发券门店字段
-- 记录发券人 user_id 和发券门店 store_id，券只能在发券门店使用（NULL=不限）
-- 幂等：使用 information_schema 检查列是否存在，不存在才 ADD

-- customer_coupons.issued_by_user_id
SET @col_exists = (SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_coupons' AND COLUMN_NAME = 'issued_by_user_id');
SET @sql = IF(@col_exists = 0,
  'ALTER TABLE customer_coupons ADD COLUMN issued_by_user_id BIGINT UNSIGNED DEFAULT NULL COMMENT ''发券人 user_id''',
  'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- customer_coupons.issued_at_store_id
SET @col_exists = (SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_coupons' AND COLUMN_NAME = 'issued_at_store_id');
SET @sql = IF(@col_exists = 0,
  'ALTER TABLE customer_coupons ADD COLUMN issued_at_store_id BIGINT UNSIGNED DEFAULT NULL COMMENT ''发券门店；NULL=不限门店使用''',
  'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 索引：按发券门店查询持券记录
SET @idx_exists = (SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_coupons' AND INDEX_NAME = 'idx_cc_issued_store');
SET @sql = IF(@idx_exists = 0,
  'ALTER TABLE customer_coupons ADD INDEX idx_cc_issued_store (issued_at_store_id)',
  'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 索引：按发券人查询持券记录
SET @idx_exists = (SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_coupons' AND INDEX_NAME = 'idx_cc_issued_by');
SET @sql = IF(@idx_exists = 0,
  'ALTER TABLE customer_coupons ADD INDEX idx_cc_issued_by (issued_by_user_id)',
  'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

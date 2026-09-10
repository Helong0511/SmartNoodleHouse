-- 门店经纬度 + 规格组按门店隔离（正式库可重复执行）
-- 依赖：stores / option_groups 已存在

SET NAMES utf8mb4;

-- stores 经纬度
SET @sql := IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'stores' AND column_name = 'latitude') = 0,
  'ALTER TABLE stores ADD COLUMN latitude DECIMAL(10,7) NULL COMMENT ''纬度（天气口味）'' AFTER phone',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'stores' AND column_name = 'longitude') = 0,
  'ALTER TABLE stores ADD COLUMN longitude DECIMAL(10,7) NULL COMMENT ''经度（天气口味）'' AFTER latitude',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- option_groups.store_id
SET @sql := IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'option_groups' AND column_name = 'store_id') = 0,
  'ALTER TABLE option_groups ADD COLUMN store_id BIGINT UNSIGNED NULL COMMENT ''NULL=总部共享；非空=门店私有'' AFTER id',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(
  (SELECT COUNT(*) FROM information_schema.table_constraints
   WHERE table_schema = DATABASE() AND table_name = 'option_groups'
     AND constraint_name = 'fk_og_store' AND constraint_type = 'FOREIGN KEY') = 0,
  'ALTER TABLE option_groups ADD CONSTRAINT fk_og_store FOREIGN KEY (store_id) REFERENCES stores(id)',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 去掉全局 code 唯一
SET @sql := IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema = DATABASE() AND table_name = 'option_groups' AND index_name = 'code') > 0,
  'ALTER TABLE option_groups DROP INDEX code',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'option_groups' AND column_name = 'store_scope') = 0,
  'ALTER TABLE option_groups ADD COLUMN store_scope BIGINT UNSIGNED AS (IFNULL(store_id, 0)) STORED COMMENT ''唯一约束用''',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema = DATABASE() AND table_name = 'option_groups' AND index_name = 'uk_og_store_code') = 0,
  'ALTER TABLE option_groups ADD UNIQUE KEY uk_og_store_code (store_scope, code)',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

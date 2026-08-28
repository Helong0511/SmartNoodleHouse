-- 顾客 JWT 撤销版本号（已有库幂等补齐）
USE smart_noodle_house;

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'customers'
    AND COLUMN_NAME = 'token_version'
);
SET @ddl := IF(
  @col_exists = 0,
  'ALTER TABLE customers ADD COLUMN token_version INT NOT NULL DEFAULT 0 COMMENT ''JWT 版本号；禁用/合并账号时递增'' AFTER status',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 顾客扫码注册门店/桌台（已有库幂等补齐）
USE smart_noodle_house;

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'customers'
    AND COLUMN_NAME = 'registered_store_id'
);
SET @ddl := IF(
  @col_exists = 0,
  'ALTER TABLE customers ADD COLUMN registered_store_id BIGINT UNSIGNED DEFAULT NULL COMMENT ''扫码注册时的门店 → stores.id；只记第一次'' AFTER token_version',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'customers'
    AND COLUMN_NAME = 'registered_table_id'
);
SET @ddl := IF(
  @col_exists = 0,
  'ALTER TABLE customers ADD COLUMN registered_table_id BIGINT UNSIGNED DEFAULT NULL COMMENT ''扫码注册时的桌台 → tables.id；只记第一次'' AFTER registered_store_id',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @idx_exists := (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'customers'
    AND INDEX_NAME = 'idx_customers_reg_store'
);
SET @ddl := IF(
  @idx_exists = 0,
  'ALTER TABLE customers ADD INDEX idx_customers_reg_store (registered_store_id)',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 历史会员：用首单门店/桌台回填（无订单则仍为空）
UPDATE customers c
JOIN (
  SELECT o.customer_id, MIN(o.id) AS first_order_id
  FROM orders o
  WHERE o.customer_id IS NOT NULL
  GROUP BY o.customer_id
) x ON x.customer_id = c.id
JOIN orders o ON o.id = x.first_order_id
SET
  c.registered_store_id = o.store_id,
  c.registered_table_id = o.table_id
WHERE c.registered_store_id IS NULL;

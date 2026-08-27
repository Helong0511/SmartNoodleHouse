-- 商品按门店隔离；编号在门店内唯一
USE smart_noodle_house;

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'products'
    AND COLUMN_NAME = 'store_id'
);

SET @sql := IF(
  @col_exists = 0,
  'ALTER TABLE products ADD COLUMN store_id BIGINT UNSIGNED NULL AFTER id, ADD INDEX idx_products_store (store_id, status, sort_order)',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 按所属分类的门店回填
UPDATE products p
JOIN product_categories c ON c.id = p.category_id
SET p.store_id = c.store_id
WHERE p.store_id IS NULL;

-- 仍空则归第一家店
UPDATE products p
JOIN (SELECT id FROM stores ORDER BY id LIMIT 1) s
SET p.store_id = s.id
WHERE p.store_id IS NULL;

-- 去掉全局 product_no 唯一，改为 (store_id, product_no)
SET @uk := (
  SELECT INDEX_NAME FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'products'
    AND COLUMN_NAME = 'product_no'
    AND NON_UNIQUE = 0
    AND INDEX_NAME != 'PRIMARY'
  LIMIT 1
);
SET @sql_drop := IF(
  @uk IS NOT NULL,
  CONCAT('ALTER TABLE products DROP INDEX `', @uk, '`'),
  'SELECT 1'
);
PREPARE stmt_drop FROM @sql_drop;
EXECUTE stmt_drop;
DEALLOCATE PREPARE stmt_drop;

SET @uk2 := (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'products'
    AND INDEX_NAME = 'uk_products_store_no'
);
SET @sql_uk := IF(
  @uk2 = 0,
  'ALTER TABLE products ADD UNIQUE KEY uk_products_store_no (store_id, product_no)',
  'SELECT 1'
);
PREPARE stmt_uk FROM @sql_uk;
EXECUTE stmt_uk;
DEALLOCATE PREPARE stmt_uk;

SET @fk := (
  SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'products'
    AND CONSTRAINT_NAME = 'fk_products_store'
);
SET @nulls := (SELECT COUNT(*) FROM products WHERE store_id IS NULL);
SET @sql_fk := IF(
  @fk = 0 AND @nulls = 0,
  'ALTER TABLE products MODIFY store_id BIGINT UNSIGNED NOT NULL, ADD CONSTRAINT fk_products_store FOREIGN KEY (store_id) REFERENCES stores(id)',
  IF(@nulls = 0, 'ALTER TABLE products MODIFY store_id BIGINT UNSIGNED NOT NULL', 'SELECT 1')
);
PREPARE stmt_fk FROM @sql_fk;
EXECUTE stmt_fk;
DEALLOCATE PREPARE stmt_fk;

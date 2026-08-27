-- 商品分类按门店隔离
USE smart_noodle_house;

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'product_categories'
    AND COLUMN_NAME = 'store_id'
);

SET @sql := IF(
  @col_exists = 0,
  'ALTER TABLE product_categories ADD COLUMN store_id BIGINT UNSIGNED NULL AFTER id, ADD INDEX idx_cat_store (store_id, sort_order, status)',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 历史数据归到第一家门店
UPDATE product_categories c
JOIN (SELECT id FROM stores ORDER BY id LIMIT 1) s
SET c.store_id = s.id
WHERE c.store_id IS NULL;

-- 若仍有空（无门店），跳过 NOT NULL；有门店则收紧
SET @has_null := (SELECT COUNT(*) FROM product_categories WHERE store_id IS NULL);
SET @sql2 := IF(
  @has_null = 0 AND @col_exists = 0,
  'ALTER TABLE product_categories MODIFY store_id BIGINT UNSIGNED NOT NULL, ADD CONSTRAINT fk_cat_store FOREIGN KEY (store_id) REFERENCES stores(id)',
  IF(
    @has_null = 0,
    -- 列已存在但可能缺 FK
    'SELECT 1',
    'SELECT 1'
  )
);
PREPARE stmt2 FROM @sql2;
EXECUTE stmt2;
DEALLOCATE PREPARE stmt2;

-- 补 FK（若不存在）
SET @fk_exists := (
  SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'product_categories'
    AND CONSTRAINT_NAME = 'fk_cat_store'
);
SET @sql3 := IF(
  @fk_exists = 0 AND (SELECT COUNT(*) FROM product_categories WHERE store_id IS NULL) = 0,
  'ALTER TABLE product_categories MODIFY store_id BIGINT UNSIGNED NOT NULL, ADD CONSTRAINT fk_cat_store FOREIGN KEY (store_id) REFERENCES stores(id)',
  'SELECT 1'
);
PREPARE stmt3 FROM @sql3;
EXECUTE stmt3;
DEALLOCATE PREPARE stmt3;

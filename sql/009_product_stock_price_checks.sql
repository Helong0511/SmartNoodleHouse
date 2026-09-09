-- 商品库存 / 选项加价数据库级约束（幂等）
-- mysql ... < sql/009_product_stock_price_checks.sql

SET @db := DATABASE();

-- products.stock >= 0
SET @sql := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.table_constraints
     WHERE table_schema = @db AND table_name = 'products' AND constraint_name = 'chk_products_stock_nonneg') = 0,
    'ALTER TABLE products ADD CONSTRAINT chk_products_stock_nonneg CHECK (stock >= 0)',
    'SELECT 1'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- products.price > 0
SET @sql := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.table_constraints
     WHERE table_schema = @db AND table_name = 'products' AND constraint_name = 'chk_products_price_pos') = 0,
    'ALTER TABLE products ADD CONSTRAINT chk_products_price_pos CHECK (price > 0)',
    'SELECT 1'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- products.original_price IS NULL OR original_price >= 0
SET @sql := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.table_constraints
     WHERE table_schema = @db AND table_name = 'products' AND constraint_name = 'chk_products_orig_price') = 0,
    'ALTER TABLE products ADD CONSTRAINT chk_products_orig_price CHECK (original_price IS NULL OR original_price >= 0)',
    'SELECT 1'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- store_product_stocks.stock >= 0
SET @sql := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.table_constraints
     WHERE table_schema = @db AND table_name = 'store_product_stocks' AND constraint_name = 'chk_sps_stock_nonneg') = 0,
    'ALTER TABLE store_product_stocks ADD CONSTRAINT chk_sps_stock_nonneg CHECK (stock >= 0)',
    'SELECT 1'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- options.extra_price >= 0
SET @sql := (
  SELECT IF(
    (SELECT COUNT(*) FROM information_schema.table_constraints
     WHERE table_schema = @db AND table_name = 'options' AND constraint_name = 'chk_options_extra_price') = 0,
    'ALTER TABLE options ADD CONSTRAINT chk_options_extra_price CHECK (extra_price >= 0)',
    'SELECT 1'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

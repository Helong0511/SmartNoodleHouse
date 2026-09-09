-- 优惠券模板门店隔离：NULL=总部全店；非空=仅该店
-- 执行：mysql ... < sql/008_coupon_template_store.sql

SET @db := DATABASE();

SET @sql := (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE coupon_templates ADD COLUMN store_id BIGINT UNSIGNED DEFAULT NULL COMMENT ''NULL=总部全店模板；非空=仅该门店'' AFTER id',
    'SELECT 1'
  )
  FROM information_schema.columns
  WHERE table_schema = @db AND table_name = 'coupon_templates' AND column_name = 'store_id'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE coupon_templates ADD INDEX idx_coupon_tmpl_store (store_id)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = @db AND table_name = 'coupon_templates' AND index_name = 'idx_coupon_tmpl_store'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE coupon_templates ADD CONSTRAINT fk_coupon_tmpl_store FOREIGN KEY (store_id) REFERENCES stores(id)',
    'SELECT 1'
  )
  FROM information_schema.table_constraints
  WHERE table_schema = @db AND table_name = 'coupon_templates' AND constraint_name = 'fk_coupon_tmpl_store'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

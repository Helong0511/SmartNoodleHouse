-- 商品级购买依赖（方案 A：商品配置优先，分类配置作默认模板）
-- 幂等：列已存在则跳过

SET @col1 = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'products'
    AND COLUMN_NAME = 'require_category_ids'
);
SET @sql1 = IF(
  @col1 = 0,
  'ALTER TABLE products ADD COLUMN require_category_ids JSON DEFAULT NULL COMMENT ''须先点过的分类 id（OR）；NULL=继承所属分类默认'' AFTER min_order_count',
  'SELECT 1'
);
PREPARE stmt1 FROM @sql1;
EXECUTE stmt1;
DEALLOCATE PREPARE stmt1;

SET @col2 = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'products'
    AND COLUMN_NAME = 'require_product_ids'
);
SET @sql2 = IF(
  @col2 = 0,
  'ALTER TABLE products ADD COLUMN require_product_ids JSON DEFAULT NULL COMMENT ''须先点过的指定商品 id（OR）；与 require_category_ids 合并 OR；NULL=未单独指定'' AFTER require_category_ids',
  'SELECT 1'
);
PREPARE stmt2 FROM @sql2;
EXECUTE stmt2;
DEALLOCATE PREPARE stmt2;

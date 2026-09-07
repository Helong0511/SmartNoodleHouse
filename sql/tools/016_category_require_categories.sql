-- 分类购买依赖：某分类商品须先点过指定分类的商品（如【加料】依赖【面】）
-- 幂等：列已存在则跳过

SET @col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'product_categories'
    AND COLUMN_NAME = 'require_category_ids'
);
SET @sql = IF(
  @col_exists = 0,
  'ALTER TABLE product_categories ADD COLUMN require_category_ids JSON DEFAULT NULL COMMENT ''须先点过的分类 id 列表（OR：满足任一即可）；NULL/空=无限制'' AFTER status',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 分类编号 + 加长商品/规格编码字段（可重复执行）
SET NAMES utf8mb4;

-- product_categories.category_no
SET @sql := IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'product_categories' AND column_name = 'category_no') = 0,
  'ALTER TABLE product_categories ADD COLUMN category_no VARCHAR(64) NOT NULL DEFAULT '''' COMMENT ''分类编号 {store_no}-逻辑'' AFTER store_id',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 按门店+拼音回填空编号（仅空串）
UPDATE product_categories c
JOIN stores s ON s.id = c.store_id
SET c.category_no = CONCAT(s.store_no, '-C', c.id)
WHERE c.category_no IS NULL OR c.category_no = '';

SET @sql := IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema = DATABASE() AND table_name = 'product_categories' AND index_name = 'uk_cat_store_no') = 0,
  'ALTER TABLE product_categories ADD UNIQUE KEY uk_cat_store_no (store_id, category_no)',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 加长 product_no
ALTER TABLE products MODIFY COLUMN product_no VARCHAR(64) NOT NULL COMMENT '店内商品编号 {store_no}-逻辑';

-- 加长规格编码
ALTER TABLE option_groups MODIFY COLUMN code VARCHAR(64) NOT NULL COMMENT '规格组编码 0000-/门店-';
ALTER TABLE options MODIFY COLUMN code VARCHAR(80) NOT NULL COMMENT '选项编码 前缀-组-项';

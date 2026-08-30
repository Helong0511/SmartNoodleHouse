-- 商品免烹饪标记（卤味等现成品，KDS 可跳过制作流程直接出餐）
-- 幂等：列已存在则跳过

SET @col_exists = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'products' AND COLUMN_NAME = 'no_cooking'
);
SET @sql = IF(
  @col_exists = 0,
  'ALTER TABLE products ADD COLUMN no_cooking TINYINT NOT NULL DEFAULT 0 COMMENT ''免烹饪/现成品：1=KDS可直出餐 0=需正常制作'' AFTER no_member_discount',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

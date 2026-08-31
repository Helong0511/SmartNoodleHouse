-- 规格选项默认值（菜单页 + 一键加购）
-- 幂等：可重复执行

SET @db := DATABASE();

SET @sql := IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
   WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'options' AND COLUMN_NAME = 'is_default') = 0,
  'ALTER TABLE options ADD COLUMN is_default TINYINT(1) NOT NULL DEFAULT 0 COMMENT ''是否默认选项（同组单选仅一个）'' AFTER sort_order',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 历史数据：编码 NORMAL 的选项设为默认
UPDATE options o
INNER JOIN option_groups g ON g.id = o.group_id
SET o.is_default = 1
WHERE o.code = 'NORMAL' AND o.status = 1 AND g.selection_type = 'SINGLE'
  AND NOT EXISTS (
    SELECT 1 FROM (SELECT group_id FROM options WHERE is_default = 1) d WHERE d.group_id = o.group_id
  );

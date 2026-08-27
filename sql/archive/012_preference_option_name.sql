-- 顾客偏好增加选项名称快照，便于展示「智能口味爱好」
USE smart_noodle_house;
SET @col := (
  SELECT COUNT(*) FROM information_schema.columns
  WHERE table_schema = DATABASE() AND table_name = 'customer_preferences' AND column_name = 'option_name'
);
SET @sql := IF(@col = 0,
  'ALTER TABLE customer_preferences ADD COLUMN option_name VARCHAR(50) DEFAULT NULL COMMENT ''选项名称快照'' AFTER option_code',
  'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

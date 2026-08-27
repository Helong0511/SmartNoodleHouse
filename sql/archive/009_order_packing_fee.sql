-- 堂食订单打包费 + 用餐方式
USE smart_noodle_house;
SET @col := (
  SELECT COUNT(*) FROM information_schema.columns
  WHERE table_schema = DATABASE() AND table_name = 'orders' AND column_name = 'packing_fee'
);
SET @sql := IF(@col = 0,
  'ALTER TABLE orders ADD COLUMN packing_fee DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT ''打包费'' AFTER discount_amount',
  'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @col2 := (
  SELECT COUNT(*) FROM information_schema.columns
  WHERE table_schema = DATABASE() AND table_name = 'orders' AND column_name = 'dining_type'
);
SET @sql2 := IF(@col2 = 0,
  'ALTER TABLE orders ADD COLUMN dining_type VARCHAR(20) NOT NULL DEFAULT ''DINE_IN'' COMMENT ''DINE_IN堂食/TAKEAWAY打包外带'' AFTER channel',
  'SELECT 1');
PREPARE stmt2 FROM @sql2; EXECUTE stmt2; DEALLOCATE PREPARE stmt2;

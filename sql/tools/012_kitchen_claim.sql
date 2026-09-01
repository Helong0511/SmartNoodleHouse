-- 厨房 KDS 软认领：开始制作时写入认领人，他人需「接管」后才能推进
-- 幂等：已存在列则跳过（MySQL 8+ 无 IF NOT EXISTS for ADD COLUMN，用存储过程友好写法见下）

SET @db := DATABASE();

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'orders' AND COLUMN_NAME = 'kitchen_claimed_by'
);
SET @sql := IF(
  @col_exists = 0,
  'ALTER TABLE orders
     ADD COLUMN kitchen_claimed_by BIGINT UNSIGNED DEFAULT NULL COMMENT ''厨房认领人 → users.id'' AFTER cancelled_at,
     ADD COLUMN kitchen_claimed_at DATETIME(3) DEFAULT NULL COMMENT ''厨房认领时间'' AFTER kitchen_claimed_by,
     ADD INDEX idx_orders_kitchen_claim (kitchen_claimed_by, status),
     ADD CONSTRAINT fk_orders_kitchen_claim FOREIGN KEY (kitchen_claimed_by) REFERENCES users(id)',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

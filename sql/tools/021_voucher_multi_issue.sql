-- 去掉 uk_cc_customer_template：同顾客同模板可持有多张（满减/折扣/代金券）
-- 欢迎券防重仍由业务层 issue_welcome_coupon 处理
-- 可在已有库单独执行

ALTER TABLE customer_coupons DROP INDEX uk_cc_customer_template;

SET @db := DATABASE();
SET @exists := (
  SELECT COUNT(1) FROM information_schema.statistics
  WHERE table_schema = @db
    AND table_name = 'customer_coupons'
    AND index_name = 'idx_cc_customer_template'
);
SET @sql := IF(
  @exists = 0,
  'ALTER TABLE customer_coupons ADD INDEX idx_cc_customer_template (customer_id, template_id)',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

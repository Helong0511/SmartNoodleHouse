-- 与 alembic 0010 对齐：券去掉每人每模板唯一；账本/积分 DB 幂等
-- 可在已有库单独执行（幂等：索引已存在会报错时可忽略）

-- 1) 优惠券：允许同模板多张
ALTER TABLE customer_coupons DROP INDEX uk_cc_customer_template;

SET @db := DATABASE();
SET @exists := (
  SELECT COUNT(1) FROM information_schema.statistics
  WHERE table_schema = @db AND table_name = 'customer_coupons' AND index_name = 'idx_cc_customer_template'
);
SET @sql := IF(@exists = 0,
  'ALTER TABLE customer_coupons ADD INDEX idx_cc_customer_template (customer_id, template_id)',
  'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 2) 财务流水幂等（NULL 不冲突）
ALTER TABLE financial_ledger ADD UNIQUE INDEX uk_fl_payment (payment_id);
ALTER TABLE financial_ledger ADD UNIQUE INDEX uk_fl_refund (refund_id);
ALTER TABLE financial_ledger ADD UNIQUE INDEX uk_fl_delivery_refund (delivery_refund_id);

-- 3) 积分流水：退款扣分唯一；EARN 用生成列
ALTER TABLE point_ledger ADD UNIQUE INDEX uk_pl_refund (refund_id);

SET @col := (
  SELECT COUNT(1) FROM information_schema.columns
  WHERE table_schema = @db AND table_name = 'point_ledger' AND column_name = 'earn_order_id'
);
SET @sql2 := IF(@col = 0,
  'ALTER TABLE point_ledger ADD COLUMN earn_order_id BIGINT UNSIGNED GENERATED ALWAYS AS (IF(entry_type = ''EARN'', order_id, NULL)) STORED',
  'SELECT 1');
PREPARE stmt2 FROM @sql2; EXECUTE stmt2; DEALLOCATE PREPARE stmt2;

ALTER TABLE point_ledger ADD UNIQUE INDEX uk_pl_earn_order (earn_order_id);

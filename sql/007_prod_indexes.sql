-- 生产统计/厨房常用复合索引（幂等：IF NOT EXISTS 风格用 information_schema 保护）
-- 执行：mysql ... < sql/007_prod_indexes.sql

SET @db := DATABASE();

-- orders: 门店 + 支付状态 + 支付时间（营收/厨房）
SET @sql := (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE orders ADD INDEX idx_orders_store_pay_paid (store_id, payment_status, paid_at)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = @db AND table_name = 'orders' AND index_name = 'idx_orders_store_pay_paid'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- payments: 状态 + 支付时间
SET @sql := (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE payments ADD INDEX idx_payments_status_paid (status, paid_at)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = @db AND table_name = 'payments' AND index_name = 'idx_payments_status_paid'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- payment_refunds: 状态 + 成功时间
SET @sql := (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE payment_refunds ADD INDEX idx_refunds_status_succ (status, succeeded_at)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = @db AND table_name = 'payment_refunds' AND index_name = 'idx_refunds_status_succ'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- financial_ledger: 门店 + 渠道 + 类型 + 时间（外卖 KPI）
SET @sql := (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE financial_ledger ADD INDEX idx_fl_store_ch_type_created (store_id, channel, entry_type, created_at)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = @db AND table_name = 'financial_ledger' AND index_name = 'idx_fl_store_ch_type_created'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- customer_coupons: 顾客 + 状态 + 过期（reserve / 列表）
SET @sql := (
  SELECT IF(
    COUNT(*) = 0,
    'ALTER TABLE customer_coupons ADD INDEX idx_cc_customer_status_exp (customer_id, status, expires_at)',
    'SELECT 1'
  )
  FROM information_schema.statistics
  WHERE table_schema = @db AND table_name = 'customer_coupons' AND index_name = 'idx_cc_customer_status_exp'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

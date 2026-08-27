-- 004: 性能索引补充（单店 5 年运营）
-- MySQL 8.0.29 以下不支持 CREATE INDEX IF NOT EXISTS，改用 information_schema 判断，可重复执行
-- 001_init_schema.sql 中已有的等价索引已跳过，见注释

USE smart_noodle_house;

-- 操作日志按时间清理
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema = DATABASE() AND table_name = 'operation_logs' AND index_name = 'idx_op_logs_created') = 0,
  'CREATE INDEX idx_op_logs_created ON operation_logs (created_at)',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 订单：001 已有 idx_orders_customer (customer_id, created_at)，跳过

-- 订单按状态+支付时间
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema = DATABASE() AND table_name = 'orders' AND index_name = 'idx_orders_status_paid') = 0,
  'CREATE INDEX idx_orders_status_paid ON orders (status, paid_at)',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 订单按门店+时间（多店扩展）
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema = DATABASE() AND table_name = 'orders' AND index_name = 'idx_orders_store_created') = 0,
  'CREATE INDEX idx_orders_store_created ON orders (store_id, created_at)',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 支付按订单+状态
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema = DATABASE() AND table_name = 'payments' AND index_name = 'idx_payments_order_status') = 0,
  'CREATE INDEX idx_payments_order_status ON payments (order_id, status)',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 支付按时间
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema = DATABASE() AND table_name = 'payments' AND index_name = 'idx_payments_created') = 0,
  'CREATE INDEX idx_payments_created ON payments (created_at)',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 001 已有 idx_welcome_customer、idx_ve_camera_time、idx_ve_expires、idx_face_customer，跳过

-- 顾客偏好按得分排序
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema = DATABASE() AND table_name = 'customer_preferences' AND index_name = 'idx_customer_prefs_score') = 0,
  'CREATE INDEX idx_customer_prefs_score ON customer_preferences (customer_id, preference_score DESC)',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

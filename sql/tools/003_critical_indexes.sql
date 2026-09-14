-- ============================================================
-- Patch 003: 关键复合索引 + 积分/财务流水幂等 UNIQUE 约束
-- ============================================================
-- P1-33: 高频查询路径补复合索引（CREATE INDEX IF NOT EXISTS，幂等可重复执行）
-- P1-50: point_ledger 增加 (order_id, entry_type) / (refund_id, entry_type) UNIQUE
--        —— 防止同一订单/退款重复写入积分流水（幂等保护）
-- P1-51: financial_ledger 增加 (payment_id, entry_type) / (refund_id, entry_type) UNIQUE
--        —— 防止同一支付/退款重复入账（幂等保护）
--
-- 兼容：MySQL 8。
-- 幂等性说明：
--   - P1-33 索引使用 CREATE INDEX IF NOT EXISTS，可重复执行。
--   - P1-50/P1-51 的 ALTER TABLE ADD UNIQUE INDEX 非幂等，重复执行会报
--     Duplicate key name；首次执行前请确认无重复数据。
-- NULL 语义说明：
--   MySQL 不支持部分索引（partial index），但 UNIQUE 索引中多个 NULL 值
--   互不相等，故 order_id / refund_id / payment_id 为 NULL 的行不会冲突，
--   天然实现「仅非 NULL 行参与幂等」的语义（等价于预期的 partial unique）。
-- ============================================================

-- ------------------------------------------------------------
-- P1-33: 复合索引（CREATE INDEX IF NOT EXISTS，幂等）
-- ------------------------------------------------------------

-- 订单：按客户 + 支付状态 + 已付时间查询（客户已付订单分页）
CREATE INDEX IF NOT EXISTS idx_orders_customer_payment_paid
  ON orders(customer_id, payment_status, paid_at, id);

-- 订单：按订单状态 + 支付状态 + 创建时间（运营/看板筛选）
CREATE INDEX IF NOT EXISTS idx_orders_status_payment_created
  ON orders(status, payment_status, created_at, id);

-- 订单：按门店 + 状态 + 创建时间（门店订单列表）
CREATE INDEX IF NOT EXISTS idx_orders_store_status_created
  ON orders(store_id, status, created_at, id);

-- 支付：按订单 + 状态（订单支付状态查询）
CREATE INDEX IF NOT EXISTS idx_payments_order_status
  ON payments(order_id, status, id);

-- Webhook inbox：按状态 + 下次重试时间（扫描待重试）
CREATE INDEX IF NOT EXISTS idx_webhook_inbox_status_retry
  ON webhook_inbox(status, next_retry_at);

-- 打印任务：按状态 + 锁定时间（stale PRINTING 回收扫描）
CREATE INDEX IF NOT EXISTS idx_print_jobs_status_locked
  ON print_jobs(status, locked_at);

-- 积分流水：按客户 + 创建时间（客户积分历史）
CREATE INDEX IF NOT EXISTS idx_point_ledger_customer_created
  ON point_ledger(customer_id, created_at);

-- ------------------------------------------------------------
-- P1-50: point_ledger 幂等 UNIQUE（order_id / refund_id + entry_type）
--   MySQL NULL 在 UNIQUE 中不冲突 → 仅非 NULL 行参与幂等
-- ------------------------------------------------------------
ALTER TABLE point_ledger
  ADD UNIQUE INDEX uq_point_order_entry (order_id, entry_type);

ALTER TABLE point_ledger
  ADD UNIQUE INDEX uq_point_refund_entry (refund_id, entry_type);

-- ------------------------------------------------------------
-- P1-51: financial_ledger 幂等 UNIQUE（payment_id / refund_id + entry_type）
--   payment_id 用于收款分录，refund_id 用于退款分录
-- ------------------------------------------------------------
ALTER TABLE financial_ledger
  ADD UNIQUE INDEX uq_financial_payment_entry (payment_id, entry_type);

ALTER TABLE financial_ledger
  ADD UNIQUE INDEX uq_financial_refund_entry (refund_id, entry_type);

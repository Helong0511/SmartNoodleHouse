-- 数据库容量评估与索引优化建议
-- 在本地 MySQL 执行: source sql/003_capacity_audit.sql

USE smart_noodle_house;

-- ========== 1. 当前表大小 ==========
SELECT
  table_name AS tbl,
  table_rows AS est_rows,
  ROUND(data_length / 1024 / 1024, 2) AS data_mb,
  ROUND(index_length / 1024 / 1024, 2) AS index_mb,
  ROUND((data_length + index_length) / 1024 / 1024, 2) AS total_mb
FROM information_schema.tables
WHERE table_schema = 'smart_noodle_house'
ORDER BY (data_length + index_length) DESC;

-- ========== 2. 推荐补充索引（单店 3~5 年够用） ==========
-- 订单按时间范围查询（Dashboard / 报表）
-- ALTER TABLE orders ADD INDEX idx_orders_created (created_at);
-- 订单按门店+时间（多店扩展）
-- ALTER TABLE orders ADD INDEX idx_orders_store_time (store_id, created_at);
-- 支付按状态+时间
-- ALTER TABLE payments ADD INDEX idx_payments_status_time (status, created_at);
-- 操作日志按时间清理
-- ALTER TABLE operation_logs ADD INDEX idx_op_logs_created (created_at);
-- 欢迎日志按时间
-- ALTER TABLE welcome_logs ADD INDEX idx_welcome_created (created_at);

-- ========== 3. 容量估算参考（单店） ==========
-- 假设: 200单/天, 每单3个明细, 每明细4个规格, 客流500次/天(保留7天)
--
-- orders:           200 * 365 * 5 ≈ 36.5万行  ~50MB
-- order_items:      ×3                      ~30MB
-- order_item_options: ×4                    ~80MB
-- payments:         200 * 365 * 5 ≈ 36.5万行  ~40MB
-- visitor_events:   500 * 7 ≈ 3500行(滚动删) ~2MB
-- operation_logs:   若不限清理 1000条/天*5年 ≈ 180万行 ~500MB ⚠️
-- welcome_logs:     50条/天*5年 ≈ 9万行      ~20MB
-- face_profiles:    会员5000人               ~5MB
--
-- 结论: 单店 5 年核心订单数据 < 300MB，主要风险在 operation_logs 无限增长

-- ========== 4. 检查 idempotency_key 唯一约束（允许多 NULL） ==========
SHOW INDEX FROM orders WHERE Key_name = 'uk_idempotency';

-- ========== 5. 检查金额字段类型（必须为 DECIMAL） ==========
SELECT table_name, column_name, column_type
FROM information_schema.columns
WHERE table_schema = 'smart_noodle_house'
  AND column_name LIKE '%amount%' OR column_name LIKE '%price%'
ORDER BY table_name, column_name;

-- ========== 6. 数据保留策略检查 ==========
SELECT setting_key, setting_value FROM system_settings
WHERE setting_key IN ('visitor_data_retention_days', 'order_payment_timeout_minutes');

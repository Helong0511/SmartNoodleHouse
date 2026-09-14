-- 005: 外卖退款台账去重
-- delivery_refunds 添加 (platform, platform_refund_id) 唯一约束
-- platform_refund_id 为 NULL 时不受约束（MySQL/SQLite 均允许多个 NULL），
-- 表示平台未提供 refund_id 的退款仍可入账，但已有 refund_id 的重复回调由 DB 拦截。
-- 幂等：使用 information_schema 检查索引是否存在，不存在才创建

SET @idx_exists = (SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'delivery_refunds'
    AND INDEX_NAME = 'uq_delivery_refund_platform_id');
SET @sql = IF(@idx_exists = 0,
  'ALTER TABLE delivery_refunds ADD UNIQUE INDEX uq_delivery_refund_platform_id (platform, platform_refund_id)',
  'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

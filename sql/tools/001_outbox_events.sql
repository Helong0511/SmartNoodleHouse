-- ============================================================
-- Patch 001: outbox_events
-- ============================================================
-- 引入 DB 事务型 outbox，用于：
--   1. 支付成功副作用（积分发放/券核销/口味重算）—— 替代易丢的 Redis list
--   2. 退款补偿（provider 成功 + 本地回滚后由 worker 重驱 finalize）
--
-- 特性：
--   - 与业务行同事务写入，commit 后即持久化（不依赖 Redis）
--   - worker 原子 claim（UPDATE...WHERE status IN('PENDING', retry-eligible 'FAILED')）
--   - stale PROCESSING 回收（locked_at < now - 5min → 复位 PENDING）
--   - 指数退避；max_attempts 后保持 FAILED 不再自动 claim
--   - 幂等键 UNIQUE(aggregate_type, aggregate_id, event_kind)
--
-- 兼容：MySQL 8 / SQLite(test)。幂等：可重复执行。
-- 回滚：DROP TABLE IF EXISTS outbox_events;
-- 范围：仅新增表，不动现有表。
-- ============================================================

CREATE TABLE IF NOT EXISTS outbox_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  aggregate_type VARCHAR(32) NOT NULL COMMENT '聚合类型：refund / paid_side_effect / ...',
  aggregate_id BIGINT UNSIGNED NOT NULL COMMENT '聚合 ID（refund_id / order_id / ...）',
  event_kind VARCHAR(64) NOT NULL COMMENT '事件类型：refund_finalize / points_award / coupon_mark_used / pref_update / points_deduct_refund / pref_recompute',
  payload MEDIUMTEXT DEFAULT NULL COMMENT 'JSON 负载（参数快照）',
  status VARCHAR(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING / PROCESSING / DONE / FAILED',
  attempt_count INT NOT NULL DEFAULT 0 COMMENT '消费尝试次数',
  max_attempts INT NOT NULL DEFAULT 8 COMMENT '最大重试次数',
  next_retry_at DATETIME(3) DEFAULT NULL COMMENT '下次允许重试时间',
  locked_at DATETIME(3) DEFAULT NULL COMMENT '消费者加锁时间',
  locked_by VARCHAR(64) DEFAULT NULL COMMENT '锁持有者标识（hostname:pid）',
  last_error VARCHAR(500) DEFAULT NULL COMMENT '最近一次错误摘要',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  processed_at DATETIME(3) DEFAULT NULL COMMENT '处理完成时间',
  UNIQUE KEY uq_outbox_agg (aggregate_type, aggregate_id, event_kind),
  INDEX idx_oe_status_retry (status, next_retry_at),
  INDEX idx_oe_status_locked (status, locked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='DB 事务型 outbox 事件';

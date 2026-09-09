-- Webhook Inbox 原子 claim / 重试字段（已有库升级）
-- 幂等：列已存在时跳过（MySQL 8 无 IF NOT EXISTS 列语法时请手工核对）

ALTER TABLE webhook_inbox
  ADD COLUMN attempt_count INT NOT NULL DEFAULT 0 COMMENT '已尝试次数（含当前 claim）' AFTER error_message,
  ADD COLUMN last_error VARCHAR(500) DEFAULT NULL COMMENT '最近一次失败摘要' AFTER attempt_count,
  ADD COLUMN next_retry_at DATETIME(3) DEFAULT NULL COMMENT '下次可重试时间（指数退避）' AFTER last_error,
  ADD COLUMN locked_at DATETIME(3) DEFAULT NULL COMMENT '当前 PROCESSING 锁定时间' AFTER next_retry_at,
  ADD COLUMN locked_by VARCHAR(64) DEFAULT NULL COMMENT '锁定 worker 标识' AFTER locked_at;

ALTER TABLE webhook_inbox
  ADD INDEX idx_wi_retry (status, next_retry_at);

-- 007: 安全加固 / 状态历史 / Webhook Inbox / 幂等
-- 可重复执行（information_schema 判断）

USE smart_noodle_house;

-- ========== 订单状态历史 ==========
CREATE TABLE IF NOT EXISTS order_status_history (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  order_id BIGINT UNSIGNED NOT NULL,
  from_status VARCHAR(20) DEFAULT NULL,
  to_status VARCHAR(20) NOT NULL,
  source VARCHAR(30) NOT NULL COMMENT 'STAFF/PAYMENT/SYSTEM/DELIVERY',
  operator_id BIGINT UNSIGNED DEFAULT NULL,
  remark VARCHAR(255) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_osh_order (order_id, created_at),
  CONSTRAINT fk_osh_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ========== 支付状态历史 ==========
CREATE TABLE IF NOT EXISTS payment_status_history (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  payment_id BIGINT UNSIGNED NOT NULL,
  from_status VARCHAR(20) DEFAULT NULL,
  to_status VARCHAR(20) NOT NULL,
  source VARCHAR(30) NOT NULL COMMENT 'CREATE/CALLBACK/REFUND/QUERY',
  remark VARCHAR(255) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_psh_payment (payment_id, created_at),
  CONSTRAINT fk_psh_payment FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ========== Webhook Inbox（先入后处理） ==========
CREATE TABLE IF NOT EXISTS webhook_inbox (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  channel VARCHAR(30) NOT NULL COMMENT 'WECHAT_PAY/ALIPAY/MEITUAN/ELEME',
  event_id VARCHAR(128) NOT NULL COMMENT '平台事件唯一 ID',
  event_type VARCHAR(50) DEFAULT NULL,
  payload MEDIUMTEXT NOT NULL,
  headers_json TEXT DEFAULT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PROCESSING/DONE/FAILED',
  error_message VARCHAR(500) DEFAULT NULL,
  received_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  processed_at DATETIME(3) DEFAULT NULL,
  UNIQUE KEY uk_webhook_event (channel, event_id),
  INDEX idx_wi_status (status, received_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ========== 支付回调 replay 审计（与 Redis 双保险） ==========
CREATE TABLE IF NOT EXISTS payment_callback_receipts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  provider VARCHAR(20) NOT NULL,
  receipt_key VARCHAR(128) NOT NULL,
  payment_no VARCHAR(32) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_receipt (provider, receipt_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ========== delivery_callback_logs 事件唯一 ==========
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema = DATABASE() AND table_name = 'delivery_callback_logs' AND index_name = 'uk_dcl_event') = 0,
  'ALTER TABLE delivery_callback_logs ADD COLUMN event_id VARCHAR(128) DEFAULT NULL, ADD UNIQUE KEY uk_dcl_event (platform, event_id)',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ========== users 门店绑定（多门店隔离） ==========
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = 'store_id') = 0,
  'ALTER TABLE users ADD COLUMN store_id BIGINT UNSIGNED DEFAULT NULL COMMENT ''NULL=全部门店(超管)''',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ========== users.token_version（JWT 撤销） ==========
SET @sql = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = 'token_version') = 0,
  'ALTER TABLE users ADD COLUMN token_version INT NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

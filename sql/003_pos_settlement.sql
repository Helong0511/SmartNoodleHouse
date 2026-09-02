-- 收银班次与日结：备用金、按操作员收款统计
-- 已有库执行：mysql ... < sql/003_pos_settlement.sql

USE smart_noodle_house;

ALTER TABLE payments
  ADD COLUMN operator_id BIGINT UNSIGNED DEFAULT NULL COMMENT '收银操作员 → users.id' AFTER customer_id;

ALTER TABLE payments
  ADD INDEX idx_payments_operator (operator_id, paid_at);

CREATE TABLE IF NOT EXISTS cashier_shifts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  operator_id BIGINT UNSIGNED NOT NULL COMMENT '收银员 → users.id',
  opening_float DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '开班备用金（元）',
  status VARCHAR(20) NOT NULL DEFAULT 'OPEN' COMMENT 'OPEN=营业中 CLOSED=已交班',
  opened_at DATETIME(3) NOT NULL COMMENT '开班时间',
  closed_at DATETIME(3) DEFAULT NULL COMMENT '交班时间',
  total_cash DECIMAL(10,2) DEFAULT NULL COMMENT '本班现金收款',
  total_wechat DECIMAL(10,2) DEFAULT NULL COMMENT '本班微信收款',
  total_alipay DECIMAL(10,2) DEFAULT NULL COMMENT '本班支付宝收款',
  total_amount DECIMAL(10,2) DEFAULT NULL COMMENT '本班收款合计',
  closing_cash DECIMAL(10,2) DEFAULT NULL COMMENT '交班实盘现金（含备用金）',
  cash_difference DECIMAL(10,2) DEFAULT NULL COMMENT '现金长短款',
  closed_by BIGINT UNSIGNED DEFAULT NULL COMMENT '确认日结的管理员 → users.id',
  remark VARCHAR(500) DEFAULT NULL COMMENT '交班备注',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  INDEX idx_shift_store_open (store_id, opened_at),
  INDEX idx_shift_operator_status (operator_id, status),
  CONSTRAINT fk_shift_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_shift_operator FOREIGN KEY (operator_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='收银班次表';

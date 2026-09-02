-- POS 退款申请：收银员申请 → 店长/超管审批并执行
-- 已有库执行：mysql ... < sql/004_pos_refund_requests.sql

USE smart_noodle_house;

CREATE TABLE IF NOT EXISTS pos_refund_requests (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  order_id BIGINT UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  payment_id BIGINT UNSIGNED NOT NULL COMMENT '主支付单（组合支付时为分摊首笔）→ payments.id',
  amount DECIMAL(10,2) NOT NULL COMMENT '申请退款金额（元）',
  executed_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '已成功执行退款合计（组合分摊）',
  reason VARCHAR(255) NOT NULL COMMENT '申请原因',
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/REJECTED/CANCELLED/SUCCESS/FAILED',
  requested_by BIGINT UNSIGNED NOT NULL COMMENT '申请人（收银员）→ users.id',
  reviewed_by BIGINT UNSIGNED DEFAULT NULL COMMENT '审批人 → users.id',
  reviewed_at DATETIME(3) DEFAULT NULL COMMENT '审批时间',
  review_remark VARCHAR(500) DEFAULT NULL COMMENT '审批备注（拒绝原因等）',
  refund_id BIGINT UNSIGNED DEFAULT NULL COMMENT '执行后的退款单 → payment_refunds.id',
  fail_reason VARCHAR(255) DEFAULT NULL COMMENT '执行失败原因',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  INDEX idx_pos_refund_store_status (store_id, status, created_at),
  INDEX idx_pos_refund_order (order_id),
  INDEX idx_pos_refund_requester (requested_by, created_at),
  CONSTRAINT fk_pos_refund_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_pos_refund_order FOREIGN KEY (order_id) REFERENCES orders(id),
  CONSTRAINT fk_pos_refund_payment FOREIGN KEY (payment_id) REFERENCES payments(id),
  CONSTRAINT fk_pos_refund_requester FOREIGN KEY (requested_by) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='收银退款申请';

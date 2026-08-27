-- 支付退款单增强：回调原文、成功时间、失败原因、状态索引
ALTER TABLE payment_refunds
  ADD COLUMN callback_raw JSON DEFAULT NULL COMMENT '平台退款回调/查询原文' AFTER provider_refund_no,
  ADD COLUMN fail_reason VARCHAR(255) DEFAULT NULL COMMENT '失败或关闭原因' AFTER callback_raw,
  ADD COLUMN succeeded_at DATETIME(3) DEFAULT NULL COMMENT '退款成功确认时间' AFTER fail_reason;

ALTER TABLE payment_refunds
  ADD INDEX idx_refunds_status (status),
  ADD INDEX idx_refunds_payment (payment_id);

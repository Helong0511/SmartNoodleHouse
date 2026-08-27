-- 退款单记录操作人，便于审计
ALTER TABLE payment_refunds
  ADD COLUMN operator_id BIGINT UNSIGNED DEFAULT NULL COMMENT '发起退款的员工用户 ID' AFTER succeeded_at,
  ADD INDEX idx_refunds_operator (operator_id);

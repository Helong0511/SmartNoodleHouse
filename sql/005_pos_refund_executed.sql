-- 组合支付退款：记录已执行金额，支持分摊失败后重试剩余
-- 已有库执行：mysql ... < sql/005_pos_refund_executed.sql

USE smart_noodle_house;

ALTER TABLE pos_refund_requests
  ADD COLUMN executed_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00
    COMMENT '已成功执行退款合计（组合分摊）' AFTER amount;

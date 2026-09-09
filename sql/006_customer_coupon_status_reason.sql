-- P1：customer_coupons.status_reason（合并冲突券标记 merge_dup）
-- 对应 alembic 0005_p1_merge_print；可在已有库上单独执行

ALTER TABLE customer_coupons
  ADD COLUMN status_reason VARCHAR(40) DEFAULT NULL
    COMMENT '状态原因：merge_dup=合并冲突作废 等';

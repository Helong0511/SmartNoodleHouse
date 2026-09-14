-- 002_coupon_quota_idempotency.sql
-- P0-6: 优惠券高并发重复发券修复
-- 1) coupon_templates 增加 total_quota / remaining（NULL=无库存限制）
-- 2) customer_coupons 增加 client_request_id + UNIQUE 索引（幂等键，NULL 多行不冲突）

ALTER TABLE coupon_templates
  ADD COLUMN total_quota INT NULL DEFAULT NULL COMMENT '发放总量上限；NULL=不限',
  ADD COLUMN remaining INT NULL DEFAULT NULL COMMENT '剩余可发量；NULL=不限';

ALTER TABLE customer_coupons
  ADD COLUMN client_request_id VARCHAR(128) NULL DEFAULT NULL COMMENT '客户端幂等键；同键重复发券只成功一次';

CREATE UNIQUE INDEX uq_cc_client_request ON customer_coupons(client_request_id);

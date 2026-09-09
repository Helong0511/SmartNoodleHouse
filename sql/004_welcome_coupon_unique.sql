-- P1：欢迎券 / 每人每模板唯一，防并发重复领取
-- 可在已有库上单独执行；新库请直接使用更新后的 001_schema.sql

DELETE cc FROM customer_coupons cc
INNER JOIN customer_coupons keep
  ON keep.customer_id = cc.customer_id
 AND keep.template_id = cc.template_id
 AND keep.id < cc.id;

-- 若索引已存在会报错，可忽略
ALTER TABLE customer_coupons
  ADD UNIQUE INDEX uk_cc_customer_template (customer_id, template_id);

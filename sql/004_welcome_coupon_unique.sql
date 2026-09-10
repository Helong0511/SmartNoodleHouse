-- P1 历史脚本：曾加每人每模板唯一
-- 已被 sql/tools/021_voucher_multi_issue.sql 取代（去掉唯一约束，同模板可多张）。
-- 勿在新环境库上再执行本文件。

DELETE cc FROM customer_coupons cc
INNER JOIN customer_coupons keep
  ON keep.customer_id = cc.customer_id
 AND keep.template_id = cc.template_id
 AND keep.id < cc.id;

ALTER TABLE customer_coupons
  ADD UNIQUE INDEX uk_cc_customer_template (customer_id, template_id);

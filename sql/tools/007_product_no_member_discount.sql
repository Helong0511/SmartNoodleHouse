-- 已有库升级：商品增加「特价不参与会员折扣」标记
ALTER TABLE products
  ADD COLUMN no_member_discount TINYINT NOT NULL DEFAULT 0
  COMMENT '特价/不参与会员折扣：1=是 0=否'
  AFTER is_hot;

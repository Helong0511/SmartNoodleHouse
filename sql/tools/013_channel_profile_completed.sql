-- 微信/支付宝渠道各自记录是否完成本端会员注册（已有库幂等补齐）
USE smart_noodle_house;

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'customer_wechat_accounts'
    AND COLUMN_NAME = 'profile_completed'
);
SET @ddl := IF(
  @col_exists = 0,
  'ALTER TABLE customer_wechat_accounts ADD COLUMN profile_completed TINYINT NOT NULL DEFAULT 0 COMMENT ''本渠道是否已完成会员注册引导'' AFTER unionid',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'customer_alipay_accounts'
    AND COLUMN_NAME = 'profile_completed'
);
SET @ddl := IF(
  @col_exists = 0,
  'ALTER TABLE customer_alipay_accounts ADD COLUMN profile_completed TINYINT NOT NULL DEFAULT 0 COMMENT ''本渠道是否已完成会员注册引导'' AFTER alipay_union_id',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 正式顾客回填：
-- 微信：有昵称即视为已在微信完成注册（含后来绑了支付宝的会员）。
-- 支付宝：仅当该顾客没有微信账号时才回填，避免「微信已注册、支付宝未完成」被标成已注册。
UPDATE customer_wechat_accounts a
JOIN customers c ON c.id = a.customer_id
SET a.profile_completed = 1
WHERE a.profile_completed = 0
  AND a.openid NOT LIKE 'wx_dev_%'
  AND (
    (c.nickname IS NOT NULL AND TRIM(c.nickname) <> '')
    OR (c.display_name IS NOT NULL AND TRIM(c.display_name) <> '')
  );

UPDATE customer_alipay_accounts a
JOIN customers c ON c.id = a.customer_id
SET a.profile_completed = 1
WHERE a.profile_completed = 0
  AND a.alipay_user_id NOT LIKE 'ali_dev_%'
  AND (
    (c.nickname IS NOT NULL AND TRIM(c.nickname) <> '')
    OR (c.display_name IS NOT NULL AND TRIM(c.display_name) <> '')
  )
  AND NOT EXISTS (
    SELECT 1 FROM customer_wechat_accounts w WHERE w.customer_id = a.customer_id
  );

-- 纠偏：若之前按「有昵称=两端都已注册」回填过，双绑顾客的支付宝必须重新走注册引导
UPDATE customer_alipay_accounts a
SET a.profile_completed = 0
WHERE EXISTS (
  SELECT 1 FROM customer_wechat_accounts w WHERE w.customer_id = a.customer_id
);

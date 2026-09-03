-- 微信/支付宝各自保存展示昵称、头像（已有库幂等补齐）
USE smart_noodle_house;

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'customer_wechat_accounts'
    AND COLUMN_NAME = 'nickname'
);
SET @ddl := IF(
  @col_exists = 0,
  'ALTER TABLE customer_wechat_accounts ADD COLUMN nickname VARCHAR(100) DEFAULT NULL COMMENT ''本渠道展示昵称（与支付宝互不覆盖）'' AFTER profile_completed',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'customer_wechat_accounts'
    AND COLUMN_NAME = 'avatar_url'
);
SET @ddl := IF(
  @col_exists = 0,
  'ALTER TABLE customer_wechat_accounts ADD COLUMN avatar_url VARCHAR(500) DEFAULT NULL COMMENT ''本渠道展示头像（与支付宝互不覆盖）'' AFTER nickname',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'customer_alipay_accounts'
    AND COLUMN_NAME = 'nickname'
);
SET @ddl := IF(
  @col_exists = 0,
  'ALTER TABLE customer_alipay_accounts ADD COLUMN nickname VARCHAR(100) DEFAULT NULL COMMENT ''本渠道展示昵称（与微信互不覆盖）'' AFTER profile_completed',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'customer_alipay_accounts'
    AND COLUMN_NAME = 'avatar_url'
);
SET @ddl := IF(
  @col_exists = 0,
  'ALTER TABLE customer_alipay_accounts ADD COLUMN avatar_url VARCHAR(500) DEFAULT NULL COMMENT ''本渠道展示头像（与微信互不覆盖）'' AFTER nickname',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 已完成本渠道注册、但尚未写入渠道资料的，从顾客表回填一份（之后两端改资料互不影响）
UPDATE customer_wechat_accounts a
JOIN customers c ON c.id = a.customer_id
SET
  a.nickname = COALESCE(NULLIF(TRIM(a.nickname), ''), NULLIF(TRIM(c.nickname), ''), NULLIF(TRIM(c.display_name), '')),
  a.avatar_url = COALESCE(NULLIF(TRIM(a.avatar_url), ''), NULLIF(TRIM(c.avatar_url), ''))
WHERE a.profile_completed = 1;

UPDATE customer_alipay_accounts a
JOIN customers c ON c.id = a.customer_id
SET
  a.nickname = COALESCE(NULLIF(TRIM(a.nickname), ''), NULLIF(TRIM(c.nickname), ''), NULLIF(TRIM(c.display_name), '')),
  a.avatar_url = COALESCE(NULLIF(TRIM(a.avatar_url), ''), NULLIF(TRIM(c.avatar_url), ''))
WHERE a.profile_completed = 1;

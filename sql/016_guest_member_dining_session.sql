-- 游客/会员状态拆分 + 就餐会话（兼容 profile_completed 旧数据）
-- 可重复执行：列/表已存在时跳过

-- ---------- customers 身份字段 ----------
SET @db := DATABASE();

SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=@db AND table_name='customers' AND column_name='member_status'),
    'SELECT 1',
    "ALTER TABLE customers ADD COLUMN member_status VARCHAR(20) NOT NULL DEFAULT 'GUEST' COMMENT 'GUEST=游客 MEMBER=已主动开通会员' AFTER status"
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=@db AND table_name='customers' AND column_name='account_status'),
    'SELECT 1',
    "ALTER TABLE customers ADD COLUMN account_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/DISABLED/DELETED' AFTER member_status"
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=@db AND table_name='customers' AND column_name='profile_status'),
    'SELECT 1',
    "ALTER TABLE customers ADD COLUMN profile_status VARCHAR(20) NOT NULL DEFAULT 'EMPTY' COMMENT 'EMPTY/PARTIAL/COMPLETED 资料完善度' AFTER account_status"
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=@db AND table_name='customers' AND column_name='phone_verified'),
    'SELECT 1',
    "ALTER TABLE customers ADD COLUMN phone_verified TINYINT NOT NULL DEFAULT 0 COMMENT '手机号是否已验证' AFTER profile_status"
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=@db AND table_name='customers' AND column_name='member_activated_at'),
    'SELECT 1',
    "ALTER TABLE customers ADD COLUMN member_activated_at DATETIME(3) NULL COMMENT '首次主动开通会员时间' AFTER phone_verified"
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=@db AND table_name='customers' AND column_name='first_seen_at'),
    'SELECT 1',
    "ALTER TABLE customers ADD COLUMN first_seen_at DATETIME(3) NULL COMMENT '首次扫码/识别时间（≠会员激活）' AFTER member_activated_at"
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=@db AND table_name='customers' AND column_name='first_seen_store_id'),
    'SELECT 1',
    "ALTER TABLE customers ADD COLUMN first_seen_store_id BIGINT UNSIGNED NULL COMMENT '首次扫码门店 → stores.id' AFTER first_seen_at"
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=@db AND table_name='customers' AND column_name='first_seen_table_id'),
    'SELECT 1',
    "ALTER TABLE customers ADD COLUMN first_seen_table_id BIGINT UNSIGNED NULL COMMENT '首次扫码桌台 → tables.id' AFTER first_seen_store_id"
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.statistics WHERE table_schema=@db AND table_name='customers' AND index_name='idx_customers_member_status'),
    'SELECT 1',
    'ALTER TABLE customers ADD INDEX idx_customers_member_status (member_status)'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 旧数据兼容：曾完成渠道注册引导 / 已有会员等级 → MEMBER
UPDATE customers c
SET
  c.member_status = 'MEMBER',
  c.member_activated_at = COALESCE(c.member_activated_at, c.created_at, CURRENT_TIMESTAMP(3)),
  c.account_status = CASE WHEN c.status = 1 THEN 'ACTIVE' WHEN c.status = 0 THEN 'DISABLED' ELSE c.account_status END
WHERE c.member_status = 'GUEST'
  AND (
    c.member_level_id IS NOT NULL
    OR EXISTS (
      SELECT 1 FROM customer_wechat_accounts w
      WHERE w.customer_id = c.id AND w.profile_completed = 1
    )
    OR EXISTS (
      SELECT 1 FROM customer_alipay_accounts a
      WHERE a.customer_id = c.id AND a.profile_completed = 1
    )
  );

UPDATE customers
SET account_status = CASE WHEN status = 1 THEN 'ACTIVE' ELSE 'DISABLED' END
WHERE account_status IS NULL OR account_status = '';

UPDATE customers
SET profile_status = CASE
  WHEN (nickname IS NOT NULL AND nickname <> '' AND phone IS NOT NULL AND phone <> '') THEN 'COMPLETED'
  WHEN (nickname IS NOT NULL AND nickname <> '') OR (phone IS NOT NULL AND phone <> '') OR (avatar_url IS NOT NULL AND avatar_url <> '') THEN 'PARTIAL'
  ELSE 'EMPTY'
END
WHERE profile_status IS NULL OR profile_status = 'EMPTY';

UPDATE customers
SET first_seen_at = COALESCE(first_seen_at, created_at),
    first_seen_store_id = COALESCE(first_seen_store_id, registered_store_id),
    first_seen_table_id = COALESCE(first_seen_table_id, registered_table_id)
WHERE first_seen_at IS NULL;

-- 历史 phone 的来源不可证明，保持 phone_verified=0；须重新走微信/支付宝官方授权。

-- ---------- dining_sessions ----------
CREATE TABLE IF NOT EXISTS dining_sessions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  customer_id BIGINT UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  table_id BIGINT UNSIGNED NOT NULL COMMENT '桌台 → tables.id',
  table_token VARCHAR(64) DEFAULT NULL COMMENT '绑定时桌台二维码 token 快照',
  status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/EXPIRED/COMPLETED/CANCELLED',
  started_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '会话开始',
  last_active_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '最近活跃',
  expires_at DATETIME(3) NOT NULL COMMENT '过期时间',
  ended_at DATETIME(3) DEFAULT NULL COMMENT '结束时间',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  INDEX idx_ds_customer_status (customer_id, status, expires_at),
  INDEX idx_ds_store_table (store_id, table_id, status),
  CONSTRAINT fk_ds_customer FOREIGN KEY (customer_id) REFERENCES customers(id),
  CONSTRAINT fk_ds_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_ds_table FOREIGN KEY (table_id) REFERENCES tables(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='就餐会话（扫码绑桌，非永久桌台归属）';

-- ---------- orders.dining_session_id ----------
SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=@db AND table_name='orders' AND column_name='dining_session_id'),
    'SELECT 1',
    "ALTER TABLE orders ADD COLUMN dining_session_id BIGINT UNSIGNED NULL COMMENT '就餐会话 → dining_sessions.id' AFTER table_id"
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.statistics WHERE table_schema=@db AND table_name='orders' AND index_name='idx_orders_dining_session'),
    'SELECT 1',
    'ALTER TABLE orders ADD INDEX idx_orders_dining_session (dining_session_id)'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 统一稳定身份 + 修复不可信手机号验证（可重复执行）
CREATE TABLE IF NOT EXISTS customer_identities (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id BIGINT UNSIGNED NOT NULL,
  provider VARCHAR(32) NOT NULL,
  provider_subject VARCHAR(128) NOT NULL,
  verified TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_customer_identity_provider_subject (provider, provider_subject),
  INDEX idx_customer_identity_customer (customer_id, provider),
  CONSTRAINT fk_customer_identity_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO customer_identities (customer_id, provider, provider_subject, verified)
SELECT customer_id, 'WECHAT_OPENID', openid, 1
FROM customer_wechat_accounts WHERE openid IS NOT NULL AND openid <> '';

INSERT IGNORE INTO customer_identities (customer_id, provider, provider_subject, verified)
SELECT MIN(customer_id), 'WECHAT_UNIONID', unionid, 1
FROM customer_wechat_accounts
WHERE unionid IS NOT NULL AND unionid <> ''
GROUP BY unionid HAVING COUNT(DISTINCT customer_id) = 1;

INSERT IGNORE INTO customer_identities (customer_id, provider, provider_subject, verified)
SELECT customer_id, 'ALIPAY_USER_ID', alipay_user_id, 1
FROM customer_alipay_accounts WHERE alipay_user_id IS NOT NULL AND alipay_user_id <> '';

UPDATE customers c
SET c.phone_verified = 0
WHERE c.phone_verified = 1
  AND NOT EXISTS (
    SELECT 1 FROM customer_identities i
    WHERE i.customer_id = c.id AND i.provider = 'PHONE'
      AND i.provider_subject = c.phone AND i.verified = 1
  );

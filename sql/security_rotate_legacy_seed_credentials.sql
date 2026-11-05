-- P0 security response for the legacy public seed values in sql/data_custom.sql.
-- Review the affected rows before execution. Take a database backup first.
-- This script is intentionally NOT included in automatic startup/migration execution.
-- Run once against the deployment that imported the legacy SH01 custom seed.

START TRANSACTION;

-- Revoke bearer QR tokens that were distributed in the public seed file.
-- Fresh installs now generate UUID-based tokens and do not seed QR image URLs.
UPDATE `tables` AS t
JOIN `stores` AS s ON s.id = t.store_id
SET
    t.qr_token = REPLACE(UUID(), '-', ''),
    t.wechat_qr_url = NULL,
    t.alipay_qr_url = NULL,
    t.updated_at = NOW(3)
WHERE s.store_no = 'SH01'
  AND t.table_no IN ('WM', 'A01', 'A02');

-- Disable the three legacy demo staff accounts and invalidate their sessions.
-- After execution, create replacement staff accounts with fresh passwords through
-- the administrator UI/bootstrap flow. Do not simply re-enable these accounts.
UPDATE `users`
SET
    status = 0,
    token_version = COALESCE(token_version, 0) + 1,
    updated_at = NOW(3)
WHERE username IN ('hsf', 'syy', 'cwm');

COMMIT;

-- Verify without displaying bearer tokens or password hashes:
SELECT
    s.store_no,
    t.table_no,
    t.status,
    (t.qr_token IS NOT NULL AND CHAR_LENGTH(t.qr_token) >= 32) AS has_rotated_token,
    (t.wechat_qr_url IS NULL AND t.alipay_qr_url IS NULL) AS qr_images_need_regeneration
FROM `tables` AS t
JOIN `stores` AS s ON s.id = t.store_id
WHERE s.store_no = 'SH01'
  AND t.table_no IN ('01', '02', '03');

SELECT username, status, token_version
FROM `users`
WHERE username IN ('hsf', 'syy', 'cwm');

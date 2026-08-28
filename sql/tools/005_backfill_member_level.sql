-- ============================================================================
-- 已注册资料但无会员等级的顾客，补挂「普通会员」
-- 幂等可重复执行
-- ============================================================================

SET NAMES utf8mb4;

UPDATE customers c
JOIN member_levels ml ON ml.name = '普通会员'
SET c.member_level_id = ml.id
WHERE c.member_level_id IS NULL
  AND (
    (c.display_name IS NOT NULL AND TRIM(c.display_name) <> '')
    OR (c.nickname IS NOT NULL AND TRIM(c.nickname) <> '')
    OR (c.phone IS NOT NULL AND TRIM(c.phone) <> '')
  );

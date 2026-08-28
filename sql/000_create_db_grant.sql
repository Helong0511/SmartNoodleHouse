-- ============================================================================
-- 建库（可选，需 MySQL root）
-- ============================================================================
-- 下一步：001_schema.sql → 002_seed_prod.sql → python scripts/init_admin.py
--
-- 应用账号请与 .env 中 DATABASE_URL 一致，示例：
--   mysql+aiomysql://remote:强密码@127.0.0.1:3306/smart_noodle_house
-- ============================================================================

CREATE DATABASE IF NOT EXISTS smart_noodle_house
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'remote'@'localhost' IDENTIFIED BY 'kb7$rL3d8!tQ';
GRANT ALL PRIVILEGES ON smart_noodle_house.* TO 'remote'@'localhost';
FLUSH PRIVILEGES;

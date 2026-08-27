-- ============================================================================
-- 智能面馆 — 建库与授权（需 MySQL root / 管理员执行）
-- ============================================================================
-- 应用账号通常无权 CREATE DATABASE，请先以 root 运行本脚本，再导入结构与种子。
--
-- 推荐导入顺序：
--   1) sql/000_create_db_grant.sql   （本文件，可选）
--   2) sql/001_schema.sql            （全量表结构）
--   3) sql/002_seed_test.sql         （测试/开发种子）
--   4) python scripts/init_admin.py  （确保 admin/admin123 密码可用）
--
-- 运维工具（非迁移）：sql/tools/003_capacity_audit.sql
-- 历史增量脚本归档：sql/archive/
-- ============================================================================

CREATE DATABASE IF NOT EXISTS smart_noodle_house
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- 将 remote 替换为你的应用账号（与 .env 中 DATABASE_URL 一致）
GRANT ALL PRIVILEGES ON smart_noodle_house.* TO 'remote'@'localhost';
FLUSH PRIVILEGES;

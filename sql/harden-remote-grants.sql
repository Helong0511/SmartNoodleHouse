-- 需用 MySQL root（或具备 GRANT 权限的账号）执行。
-- 收紧应用账号：仅本库、无 GRANT OPTION、不跨库。
-- 口令请与 .env DATABASE_URL 保持一致；轮换时两端一起改。

REVOKE ALL PRIVILEGES, GRANT OPTION ON *.* FROM 'remote'@'localhost';
REVOKE ALL PRIVILEGES, GRANT OPTION ON `platform`.* FROM 'remote'@'localhost';

GRANT SELECT, INSERT, UPDATE, DELETE,
  CREATE, ALTER, DROP, INDEX, REFERENCES, TRIGGER,
  CREATE TEMPORARY TABLES, EXECUTE, SHOW VIEW, LOCK TABLES
  ON `smart_noodle_house`.* TO 'remote'@'localhost';

FLUSH PRIVILEGES;
SHOW GRANTS FOR 'remote'@'localhost';

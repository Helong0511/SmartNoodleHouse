-- 多店续：员工权限 + 分店库存 + 门店业务参数
USE smart_noodle_house;

INSERT INTO permissions (code, name, module)
SELECT 'user:read', '查看员工', 'user' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'user:read');

INSERT INTO permissions (code, name, module)
SELECT 'user:manage', '管理员工', 'user' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'user:manage');

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.code IN ('user:read', 'user:manage')
WHERE r.code = 'SUPER_ADMIN'
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.code IN ('user:read', 'user:manage', 'product:update')
WHERE r.code = 'STORE_ADMIN'
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

CREATE TABLE IF NOT EXISTS store_product_stocks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  store_id BIGINT UNSIGNED NOT NULL,
  product_id BIGINT UNSIGNED NOT NULL,
  stock INT NOT NULL DEFAULT 999 COMMENT '分店库存，999=不限',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1本店在售 0本店停售',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_store_product (store_id, product_id),
  INDEX idx_sps_product (product_id),
  CONSTRAINT fk_sps_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_sps_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS store_settings (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  store_id BIGINT UNSIGNED NOT NULL,
  setting_key VARCHAR(100) NOT NULL,
  setting_value VARCHAR(500) NOT NULL,
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_store_setting (store_id, setting_key),
  CONSTRAINT fk_ss_store FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

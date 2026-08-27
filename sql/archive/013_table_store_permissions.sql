-- 多店：桌台管理权限 + 门店管理员授权
USE smart_noodle_house;

INSERT INTO permissions (code, name, module)
SELECT 'table:manage', '管理桌台', 'table'
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'table:manage');

-- 超管补齐新权限
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.code = 'table:manage'
WHERE r.code = 'SUPER_ADMIN'
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

-- 门店管理员：本店桌台 + 摄像头 + 迎宾 + 顾客/支付只读（不给 system:manage，避免改全局配置）
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.code IN (
  'table:manage',
  'camera:read',
  'camera:manage',
  'welcome:manage',
  'customer:read',
  'payment:read',
  'product:read'
)
WHERE r.code = 'STORE_ADMIN'
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

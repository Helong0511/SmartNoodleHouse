-- 已有库升级：后台可调整银卡/金卡升级积分门槛
INSERT INTO permissions (code, name, module)
SELECT 'customer:manage', '管理会员等级', 'customer' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'customer:manage');

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r
JOIN permissions p ON p.code = 'customer:manage'
WHERE r.code IN ('SUPER_ADMIN', 'STORE_ADMIN')
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

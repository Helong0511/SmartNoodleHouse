-- 厨房角色：可查看/推进订单（KDS）
USE smart_noodle_house;
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.code IN ('order:read', 'order:update')
WHERE r.code = 'KITCHEN'
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

-- 门店管理员也确保有订单读写（若种子不全）
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.code IN ('order:read', 'order:update')
WHERE r.code = 'STORE_ADMIN'
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

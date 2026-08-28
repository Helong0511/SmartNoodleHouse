-- ============================================================================
-- 智能面馆 — 生产环境基础种子（角色/权限/会员等级/券模板/门店/系统参数）
-- ============================================================================
-- 适用：已执行 001_schema.sql 的空库
-- 不包含：默认弱口令账号、可预测桌台 token、测试顾客/订单
-- 部署后务必执行：python scripts/init_admin.py  创建强密码管理员
-- ============================================================================

SET NAMES utf8mb4;
USE smart_noodle_house;

-- 一、角色
INSERT INTO roles (code, name, description)
SELECT 'SUPER_ADMIN', '超级管理员', '跨店全部权限'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'SUPER_ADMIN');

INSERT INTO roles (code, name, description)
SELECT 'STORE_ADMIN', '门店管理员', '本店运营（不含全局系统配置）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'STORE_ADMIN');

INSERT INTO roles (code, name, description)
SELECT 'CASHIER', '收银员', '订单与支付'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'CASHIER');

INSERT INTO roles (code, name, description)
SELECT 'KITCHEN', '厨房', 'KDS 出餐'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'KITCHEN');

INSERT INTO roles (code, name, description)
SELECT 'OPERATOR', '运营', '商品与数据'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'OPERATOR');

-- 二、权限点
INSERT INTO permissions (code, name, module)
SELECT 'product:read', '查看商品', 'product' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'product:read');
INSERT INTO permissions (code, name, module)
SELECT 'product:create', '创建商品', 'product' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'product:create');
INSERT INTO permissions (code, name, module)
SELECT 'product:update', '更新商品', 'product' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'product:update');
INSERT INTO permissions (code, name, module)
SELECT 'product:delete', '删除商品', 'product' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'product:delete');
INSERT INTO permissions (code, name, module)
SELECT 'order:read', '查看订单', 'order' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'order:read');
INSERT INTO permissions (code, name, module)
SELECT 'order:update', '更新订单', 'order' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'order:update');
INSERT INTO permissions (code, name, module)
SELECT 'customer:read', '查看顾客', 'customer' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'customer:read');
INSERT INTO permissions (code, name, module)
SELECT 'camera:read', '查看摄像头', 'camera' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'camera:read');
INSERT INTO permissions (code, name, module)
SELECT 'camera:manage', '管理摄像头', 'camera' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'camera:manage');
INSERT INTO permissions (code, name, module)
SELECT 'welcome:manage', '迎宾管理', 'welcome' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'welcome:manage');
INSERT INTO permissions (code, name, module)
SELECT 'payment:read', '查看支付', 'payment' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'payment:read');
INSERT INTO permissions (code, name, module)
SELECT 'payment:refund', '发起退款', 'payment' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'payment:refund');
INSERT INTO permissions (code, name, module)
SELECT 'table:manage', '管理桌台', 'table' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'table:manage');
INSERT INTO permissions (code, name, module)
SELECT 'user:read', '查看员工', 'user' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'user:read');
INSERT INTO permissions (code, name, module)
SELECT 'user:manage', '管理员工', 'user' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'user:manage');
INSERT INTO permissions (code, name, module)
SELECT 'system:manage', '系统设置', 'system' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'system:manage');
INSERT INTO permissions (code, name, module)
SELECT 'coupon:read', '查看优惠券', 'coupon' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'coupon:read');
INSERT INTO permissions (code, name, module)
SELECT 'coupon:manage', '管理优惠券', 'coupon' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'coupon:manage');

-- 三、角色权限矩阵
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
WHERE r.code = 'SUPER_ADMIN'
  AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id);

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r
JOIN permissions p ON p.code IN (
  'product:read', 'product:create', 'product:update', 'product:delete',
  'order:read', 'order:update', 'customer:read',
  'camera:read', 'camera:manage', 'welcome:manage',
  'payment:read', 'payment:refund', 'table:manage',
  'user:read', 'user:manage', 'coupon:read', 'coupon:manage'
)
WHERE r.code = 'STORE_ADMIN'
  AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id);

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r
JOIN permissions p ON p.code IN ('order:read', 'order:update')
WHERE r.code = 'KITCHEN'
  AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id);

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r
JOIN permissions p ON p.code IN (
  'order:read', 'order:update', 'payment:read', 'payment:refund', 'customer:read', 'product:read'
)
WHERE r.code = 'CASHIER'
  AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id);

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r
JOIN permissions p ON p.code IN (
  'product:read', 'product:create', 'product:update', 'product:delete',
  'order:read', 'customer:read', 'payment:read'
)
WHERE r.code = 'OPERATOR'
  AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id);

-- 四、会员等级
INSERT INTO member_levels (name, min_points, discount_rate)
SELECT '普通会员', 0, 1.0000 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM member_levels WHERE name = '普通会员');
INSERT INTO member_levels (name, min_points, discount_rate)
SELECT '银卡会员', 100, 0.9800 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM member_levels WHERE name = '银卡会员');
INSERT INTO member_levels (name, min_points, discount_rate)
SELECT '金卡会员', 500, 0.9500 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM member_levels WHERE name = '金卡会员');

-- 五、优惠券模板
INSERT INTO coupon_templates (name, coupon_type, discount_value, min_order_amount, valid_days)
SELECT '新客满30减5', 'FIXED', 5.00, 30.00, 30 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM coupon_templates WHERE name = '新客满30减5');
INSERT INTO coupon_templates (name, coupon_type, discount_value, min_order_amount, valid_days)
SELECT '满50享9折', 'PERCENT', 0.9000, 50.00, 14 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM coupon_templates WHERE name = '满50享9折');
INSERT INTO coupon_templates (name, coupon_type, discount_value, min_order_amount, valid_days)
SELECT '10元无门槛代金券', 'VOUCHER', 10.00, 0.00, 30 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM coupon_templates WHERE name = '10元无门槛代金券');

-- 六、门店（请按实际修改名称/地址）
INSERT INTO stores (store_no, name, address, phone, status)
SELECT 'SH01', '智能面馆总店', '请修改为实际地址', '400-000-0000', 1
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM stores WHERE store_no = 'SH01');

-- 七、系统业务参数
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'order_payment_timeout_minutes', '15', '订单支付超时分钟数'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'order_payment_timeout_minutes');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'visitor_data_retention_days', '7', '匿名访客数据保留天数'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'visitor_data_retention_days');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'welcome_cooldown_seconds', '60', '迎宾冷却秒数'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'welcome_cooldown_seconds');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'face_match_threshold', '0.75', '人脸匹配阈值'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'face_match_threshold');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'face_confirm_frames', '3', '人脸确认帧数'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'face_confirm_frames');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'operation_log_retention_days', '90', '操作日志保留天数'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'operation_log_retention_days');

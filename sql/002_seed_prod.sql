-- ============================================================================
-- 智能面馆 — 生产环境种子数据
-- ============================================================================
-- 前置：sql/001_schema.sql
-- 后续：python scripts/init_admin.py（创建超管，勿写入弱口令 SQL）
--
-- payment:refund 仅 SUPER_ADMIN / STORE_ADMIN
-- 多门店：第六节按实际增删 store_no
-- 幂等：INSERT … WHERE NOT EXISTS；第七节校正历史误配
-- ============================================================================

SET NAMES utf8mb4;
USE smart_noodle_house;

-- 一、角色
INSERT INTO roles (code, name, description)
SELECT 'SUPER_ADMIN', '超级管理员', '跨店全部权限'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'SUPER_ADMIN');

INSERT INTO roles (code, name, description)
SELECT 'STORE_ADMIN', '门店管理员', '本店运营（含退款；不含全局系统配置）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'STORE_ADMIN');

INSERT INTO roles (code, name, description)
SELECT 'CASHIER', '收银员', '订单与支付（不可退款）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'CASHIER');

INSERT INTO roles (code, name, description)
SELECT 'KITCHEN', '厨房', 'KDS 出餐（不可退款/不可审外卖退款）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'KITCHEN');

INSERT INTO roles (code, name, description)
SELECT 'OPERATOR', '运营', '商品与数据（不可退款）'
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
SELECT 'customer:manage', '管理会员等级', 'customer' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'customer:manage');
INSERT INTO permissions (code, name, module)
SELECT 'camera:read', '查看摄像头', 'camera' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'camera:read');
INSERT INTO permissions (code, name, module)
SELECT 'camera:manage', '管理摄像头', 'camera' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'camera:manage');
INSERT INTO permissions (code, name, module)
SELECT 'welcome:manage', '管理迎宾', 'welcome' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'welcome:manage');
INSERT INTO permissions (code, name, module)
SELECT 'payment:read', '查看支付', 'payment' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'payment:read');
INSERT INTO permissions (code, name, module)
SELECT 'payment:refund', '支付与外卖退款', 'payment' FROM DUAL
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
SELECT 'system:manage', '系统管理', 'system' FROM DUAL
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
  'order:read', 'order:update', 'customer:read', 'customer:manage',
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
  'order:read', 'order:update', 'payment:read', 'customer:read', 'product:read'
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
SELECT '银卡会员', 200, 0.9800 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM member_levels WHERE name = '银卡会员');
INSERT INTO member_levels (name, min_points, discount_rate)
SELECT '金卡会员', 400, 0.9500 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM member_levels WHERE name = '金卡会员');

-- 五、券模板（VOUCHER 一次性核销；应付须≥面额）
INSERT INTO coupon_templates (name, coupon_type, discount_value, min_order_amount, valid_days)
SELECT '新客满30减5', 'FIXED', 5.00, 30.00, 30 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM coupon_templates WHERE name = '新客满30减5');

INSERT INTO coupon_templates (name, coupon_type, discount_value, min_order_amount, valid_days)
SELECT '满50享9折', 'PERCENT', 0.9000, 50.00, 14 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM coupon_templates WHERE name = '满50享9折');

INSERT INTO coupon_templates (name, coupon_type, discount_value, min_order_amount, valid_days)
SELECT '10元无门槛代金券', 'VOUCHER', 10.00, 0.00, 30 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM coupon_templates WHERE name = '10元无门槛代金券');

-- 六、门店
INSERT INTO stores (store_no, name, address, phone, status)
SELECT 'SH01', '智能面馆总店', '请修改为实际地址', '400-001-0001', 1
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM stores WHERE store_no = 'SH01');

INSERT INTO stores (store_no, name, address, phone, status)
SELECT 'SH02', '智能面馆分店', '请修改为分店地址', '400-001-0002', 1
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM stores WHERE store_no = 'SH02');

-- 七、系统参数
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
SELECT 'face_confirm_frames', '5', '人脸确认帧数'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'face_confirm_frames');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'operation_log_retention_days', '60', '操作日志保留天数'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'operation_log_retention_days');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'kitchen_auto_complete_minutes', '20', '厨房 KDS 未处理自动出餐（分钟，0=关闭）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'kitchen_auto_complete_minutes');

-- 八、校正（幂等）：非店长角色不得持有 payment:refund
DELETE rp FROM role_permissions rp
JOIN roles r ON r.id = rp.role_id
JOIN permissions p ON p.id = rp.permission_id
WHERE r.code IN ('CASHIER', 'KITCHEN', 'OPERATOR') AND p.code = 'payment:refund';

UPDATE permissions SET name = '支付与外卖退款' WHERE code = 'payment:refund';

SELECT
  (SELECT COUNT(*) FROM roles) AS roles,
  (SELECT COUNT(*) FROM permissions) AS permissions,
  (SELECT COUNT(*) FROM stores WHERE status = 1) AS active_stores;

-- 添加管理员账号
INSERT INTO users (username, password_hash, display_name, role_id, store_id, status) VALUES ('admin', '$2b$12$38dPQAo/7seG5MQ9uCaLg.eysxss7vHFFOYSORWyAO5sNflaFgaQm', '系统管理员', 1, NULL, 1);

-- 添加分类
INSERT INTO `smart_noodle_house`.`product_categories` (`store_id`, `name`, `description`, `sort_order`, `status`, `created_at`, `updated_at`) VALUES (1, '特价品', '超级优惠', 0, 1, '2026-08-31 11:30:51.435', '2026-08-31 11:30:51.435');
INSERT INTO `smart_noodle_house`.`product_categories` (`store_id`, `name`, `description`, `sort_order`, `status`, `created_at`, `updated_at`) VALUES (1, '绵阳米粉', '经典绵阳米粉', 0, 1, '2026-08-31 11:30:51.435', '2026-08-31 11:30:51.435');
INSERT INTO `smart_noodle_house`.`product_categories` (`store_id`, `name`, `description`, `sort_order`, `status`, `created_at`, `updated_at`) VALUES (1, '面', '面', 0, 1, '2026-08-31 11:30:51.435', '2026-08-31 11:30:51.435');
INSERT INTO `smart_noodle_house`.`product_categories` (`store_id`, `name`, `description`, `sort_order`, `status`, `created_at`, `updated_at`) VALUES (1, '小吃', '小吃', 0, 1, '2026-08-31 11:30:51.435', '2026-08-31 11:30:51.435');
INSERT INTO `smart_noodle_house`.`product_categories` (`store_id`, `name`, `description`, `sort_order`, `status`, `created_at`, `updated_at`) VALUES (1, '卤味', '卤味', 0, 1, '2026-08-31 11:30:51.435', '2026-08-31 11:30:51.435');
INSERT INTO `smart_noodle_house`.`product_categories` (`store_id`, `name`, `description`, `sort_order`, `status`, `created_at`, `updated_at`) VALUES (1, '饮料', '饮料', 0, 1, '2026-08-31 11:30:51.435', '2026-08-31 11:30:51.435');

-- 添加规格租
INSERT INTO `smart_noodle_house`.`option_groups` (`name`, `code`, `selection_type`, `is_required`, `sort_order`, `status`, `created_at`, `updated_at`) VALUES ('量', 'Liang', 'SINGLE', 1, 0, 1, '2026-08-31 11:53:14.384', '2026-08-31 11:53:14.384');
INSERT INTO `smart_noodle_house`.`option_groups` (`name`, `code`, `selection_type`, `is_required`, `sort_order`, `status`, `created_at`, `updated_at`) VALUES ('加料', 'JiaLiao', 'MULTIPLE', 0, 1, 1, '2026-08-31 11:53:14.384', '2026-08-31 11:53:14.384');
INSERT INTO `smart_noodle_house`.`option_groups` (`name`, `code`, `selection_type`, `is_required`, `sort_order`, `status`, `created_at`, `updated_at`) VALUES ('辣', 'La', 'SINGLE', 1, 2, 1, '2026-08-31 11:53:14.384', '2026-08-31 11:53:14.384');
INSERT INTO `smart_noodle_house`.`option_groups` (`name`, `code`, `selection_type`, `is_required`, `sort_order`, `status`, `created_at`, `updated_at`) VALUES ('麻', 'Ma', 'SINGLE', 1, 3, 1, '2026-08-31 11:53:14.384', '2026-08-31 11:53:14.384');
INSERT INTO `smart_noodle_house`.`option_groups` (`name`, `code`, `selection_type`, `is_required`, `sort_order`, `status`, `created_at`, `updated_at`) VALUES ('蒜末', 'SuanMo', 'SINGLE', 1, 4, 1, '2026-08-31 11:53:14.384', '2026-08-31 11:53:14.384');

-- 添加规格组明细
  -- 量
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (1, '小碗', 'Liang-XiaoWan', 0.00, 0, 1, 1, '2026-08-31 11:53:36.282', '2026-08-31 11:53:47.000');
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (1, '中碗', 'Liang-ZhongWan', 1.00, 1, 0, 1, '2026-08-31 11:54:09.206', '2026-08-31 11:54:09.206');
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (1, '大碗', 'Liang-DaWAN', 2.00, 2, 0, 1, '2026-08-31 11:54:26.517', '2026-08-31 11:54:26.517');

  -- 加料
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (2, '加卤蛋', 'JiaLiao-LuDan', 2.00, 0, 0, 1, '2026-08-31 11:53:36.282', '2026-08-31 11:53:47.000');
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (2, '加牛肉', 'JiaLiao-NiuRou', 5.00, 1, 0, 1, '2026-08-31 11:54:09.206', '2026-08-31 11:54:09.206');
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (2, '加肥肠', 'JiaLiao-FeiChang', 5.00, 2, 0, 1, '2026-08-31 11:54:26.517', '2026-08-31 11:54:26.517');

  -- 辣
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (3, '不辣', 'La-BuLa', 0.00, 0, 1, 1, '2026-08-31 11:53:36.282', '2026-08-31 11:53:47.000');
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (3, '微辣', 'La-WeiLa', 0.00, 1, 0, 1, '2026-08-31 11:54:09.206', '2026-08-31 11:54:09.206');
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (3, '正常辣', 'La-ZhengChangLa', 0.00, 2, 0, 1, '2026-08-31 11:54:26.517', '2026-08-31 11:54:26.517');
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (3, '加辣', 'La-JiaLa', 0.00, 3, 0, 1, '2026-08-31 11:54:26.517', '2026-08-31 11:54:26.517');

  -- 麻
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (4, '不麻', 'Ma-BuMa', 0.00, 0, 1, 1, '2026-08-31 11:53:36.282', '2026-08-31 11:53:47.000');
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (4, '微麻', 'Ma-WeiMa', 0.00, 1, 0, 1, '2026-08-31 11:54:09.206', '2026-08-31 11:54:09.206');
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (4, '正常麻', 'Ma-ZhengChangMa', 0.00, 2, 0, 1, '2026-08-31 11:54:26.517', '2026-08-31 11:54:26.517');
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (4, '加麻', 'Ma-JiaMa', 0.00, 3, 0, 1, '2026-08-31 11:54:26.517', '2026-08-31 11:54:26.517');

  -- 蒜
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (5, '不加蒜末', 'SuanMo-BuJiaSuanMo', 0.00, 0, 1, 1, '2026-08-31 11:53:36.282', '2026-08-31 11:53:47.000');
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (5, '微量蒜末', 'SuanMo-WeiLiangSuanMo', 0.00, 1, 0, 1, '2026-08-31 11:54:09.206', '2026-08-31 11:54:09.206');
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (5, '正常蒜末', 'SuanMo-ZhengChangSuanMo', 0.00, 2, 0, 1, '2026-08-31 11:54:26.517', '2026-08-31 11:54:26.517');
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES (5, '加量蒜末', 'SuanMo-JiaLiangSuanMo', 0.00, 3, 0, 1, '2026-08-31 11:54:26.517', '2026-08-31 11:54:26.517');

-- 添加餐品
INSERT INTO products (
  store_id, category_id, product_no, name, description, main_image,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count
)
SELECT s.id, c.id, 'M001', '牛肉面', '好吃的牛肉面', '/uploads/products/25985ea8d2fe4d08b074f5b4e443d57d.png',
  7.00, 6.00, 9999, 1.00, 1,
  'ON_SALE', 1, 1, 0, 0, 0, 1
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '面'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'M001');

INSERT INTO products (
  store_id, category_id, product_no, name, description, main_image,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count
)
SELECT s.id, c.id, 'M002', '肥肠面', '好吃的肥肠面', '/uploads/products/8681823f4fd646789b1915d1a82b4eab.png',
  7.00, 6.00, 9999, 1.00, 1,
  'ON_SALE', 1, 1, 0, 0, 0, 1
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '面'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'M002');

INSERT INTO products (
  store_id, category_id, product_no, name, description, main_image,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count
)
SELECT s.id, c.id, 'F001', '绵阳米粉（牛肉）', '好吃的牛肉米粉', '/uploads/products/77ab8543ca8a445885909f048a946e46.png',
  7.00, 6.00, 9999, 1.00, 1,
  'ON_SALE', 1, 1, 0, 0, 0, 1
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '绵阳米粉'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'F001');

INSERT INTO products (
  store_id, category_id, product_no, name, description, main_image,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count
)
SELECT s.id, c.id, 'F002', '绵阳米粉（肥肠）', '好吃的肥肠米粉', '/uploads/products/12537c63a3b84e0da4eae8ef570b6ae4.png',
  7.00, 6.00, 9999, 1.00, 1,
  'ON_SALE', 1, 1, 0, 0, 0, 1
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '绵阳米粉'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'F002');

-- 添加桌号
INSERT INTO `smart_noodle_house`.`tables` (`id`, `store_id`, `table_no`, `table_name`, `qr_token`, `wechat_qr_url`, `alipay_qr_url`, `status`, `created_at`, `updated_at`) VALUES 
(1, 1, 'WM', '外卖订单', 'cAY3TvcOi5iDOLXVL9q6CokznisJTflR6chIcFnQvI4', 'weixin://dl/business/?appid=YOUR_WECHAT_APPID&path=pages/index/index&query=token%3DcAY3TvcOi5iDOLXVL9q6CokznisJTflR6chIcFnQvI4%26channel%3Dwechat', 'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages/index/index&query=token%3DcAY3TvcOi5iDOLXVL9q6CokznisJTflR6chIcFnQvI4%26channel%3Dalipay', 1, '2026-08-31 12:25:41.769', '2026-08-31 12:25:41.769'),
(2, 1, 'A01', '1号桌', 'cbqIjy3tT-ZaulwX9K0ICJ3LBwYTsO1H4MJPaooc3sE', 'weixin://dl/business/?appid=YOUR_WECHAT_APPID&path=pages/index/index&query=token%3DcbqIjy3tT-ZaulwX9K0ICJ3LBwYTsO1H4MJPaooc3sE%26channel%3Dwechat', 'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages/index/index&query=token%3DcbqIjy3tT-ZaulwX9K0ICJ3LBwYTsO1H4MJPaooc3sE%26channel%3Dalipay', 1, '2026-08-28 12:38:58.111', '2026-08-28 12:44:52.000'),
(3, 1, 'A02', '2号桌', 'J7aG8hvHA1lg2QUldWnYvV9yX9Y_C0y3ZLxVfZsHS-g', 'weixin://dl/business/?appid=YOUR_WECHAT_APPID&path=pages/index/index&query=token%3DJ7aG8hvHA1lg2QUldWnYvV9yX9Y_C0y3ZLxVfZsHS-g%26channel%3Dwechat', 'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages/index/index&query=token%3DJ7aG8hvHA1lg2QUldWnYvV9yX9Y_C0y3ZLxVfZsHS-g%26channel%3Dalipay', 1, '2026-08-28 12:38:58.117', '2026-08-28 12:44:53.000');

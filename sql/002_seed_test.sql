-- ============================================================================
-- 智能面馆 SmartNoodleHouse — 测试 / 开发种子数据
-- ============================================================================
-- 适用：已执行 001_schema.sql 的空库或可幂等补齐的开发库
-- 说明：合并原 002 / 010 / 011 / 013 / 014 / 017 中业务种子与权限矩阵
-- 默认管理员：admin / admin123（若无法登录请再执行 python scripts/init_admin.py）
-- 门店收银：cashier / admin123（绑定 SNH001，退款操作人演示）
-- 开发桌台扫码：token=1001 / 1002 / 1003
-- 支付/退款样例单号：SEED-ORD-* / SEED-PAY-* / SEED-RF-*（幂等，可重复执行）
-- ============================================================================

SET NAMES utf8mb4;
USE smart_noodle_house;

-- ============================================================================
-- 一、角色
-- ============================================================================
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

-- ============================================================================
-- 二、权限点（全量）
-- ============================================================================
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
SELECT 'welcome:manage', '管理迎宾', 'welcome' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'welcome:manage');

INSERT INTO permissions (code, name, module)
SELECT 'payment:read', '查看支付', 'payment' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'payment:read');
INSERT INTO permissions (code, name, module)
SELECT 'payment:refund', '支付退款', 'payment' FROM DUAL
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

-- ============================================================================
-- 三、角色权限矩阵
-- ============================================================================

-- 3.1 超级管理员：全部权限
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
CROSS JOIN permissions p
WHERE r.code = 'SUPER_ADMIN'
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

-- 3.2 门店管理员：本店运营（不含 system:manage）
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.code IN (
  'product:read', 'product:create', 'product:update', 'product:delete',
  'order:read', 'order:update',
  'customer:read',
  'camera:read', 'camera:manage',
  'welcome:manage',
  'payment:read', 'payment:refund',
  'table:manage',
  'user:read', 'user:manage'
)
WHERE r.code = 'STORE_ADMIN'
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

-- 3.3 厨房：KDS 读写订单
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.code IN ('order:read', 'order:update')
WHERE r.code = 'KITCHEN'
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

-- 3.4 收银：订单 + 支付
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.code IN (
  'order:read', 'order:update',
  'payment:read', 'payment:refund',
  'customer:read', 'product:read'
)
WHERE r.code = 'CASHIER'
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

-- 3.5 运营：商品为主
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.code IN (
  'product:read', 'product:create', 'product:update', 'product:delete',
  'order:read', 'customer:read', 'payment:read'
)
WHERE r.code = 'OPERATOR'
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

-- ============================================================================
-- 四、会员等级
-- ============================================================================
INSERT INTO member_levels (name, min_points, discount_rate)
SELECT '普通会员', 0, 1.0000 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM member_levels WHERE name = '普通会员');

INSERT INTO member_levels (name, min_points, discount_rate)
SELECT '银卡会员', 100, 0.9800 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM member_levels WHERE name = '银卡会员');

INSERT INTO member_levels (name, min_points, discount_rate)
SELECT '金卡会员', 500, 0.9500 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM member_levels WHERE name = '金卡会员');

-- ============================================================================
-- 五、门店（统一编号 SNH001）
-- ============================================================================
INSERT INTO stores (store_no, name, address, phone, status)
SELECT 'SNH001', '智能面馆总店', '太和大道100号', '400-000-0000', 1
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM stores WHERE store_no = 'SNH001');

-- ============================================================================
-- 六、默认超级管理员 admin / admin123
-- password_hash 为 bcrypt；登录异常时执行：python scripts/init_admin.py
-- ============================================================================
INSERT INTO users (username, password_hash, display_name, role_id, store_id, status)
SELECT
  'admin',
  '$2b$12$38dPQAo/7seG5MQ9uCaLg.eysxss7vHFFOYSORWyAO5sNflaFgaQm',
  '系统管理员',
  r.id,
  NULL,
  1
FROM roles r
WHERE r.code = 'SUPER_ADMIN'
  AND NOT EXISTS (SELECT 1 FROM users WHERE username = 'admin');

-- 门店收银（与 admin 同密码 admin123；用于退款 operator_id 展示）
INSERT INTO users (username, password_hash, display_name, role_id, store_id, status)
SELECT
  'cashier',
  '$2b$12$38dPQAo/7seG5MQ9uCaLg.eysxss7vHFFOYSORWyAO5sNflaFgaQm',
  '总店收银',
  r.id,
  s.id,
  1
FROM roles r
CROSS JOIN stores s
WHERE r.code = 'CASHIER'
  AND s.store_no = 'SNH001'
  AND NOT EXISTS (SELECT 1 FROM users WHERE username = 'cashier');

-- ============================================================================
-- 七、开发桌台（扫码 token = 桌号）
-- 小程序/H5：?token=1001 即可点餐；无桌台时下单会报「无效桌台二维码」
-- ============================================================================
INSERT INTO `tables` (store_id, table_no, table_name, qr_token, wechat_qr_url, alipay_qr_url, status)
SELECT s.id, '1001', '1号桌', '1001',
  'pages/index/index?token=1001&channel=wechat',
  'pages/index/index?token=1001&channel=alipay',
  1
FROM stores s
WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (SELECT 1 FROM `tables` t WHERE t.store_id = s.id AND t.table_no = '1001');

INSERT INTO `tables` (store_id, table_no, table_name, qr_token, wechat_qr_url, alipay_qr_url, status)
SELECT s.id, '1002', '2号桌', '1002',
  'pages/index/index?token=1002&channel=wechat',
  'pages/index/index?token=1002&channel=alipay',
  1
FROM stores s
WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (SELECT 1 FROM `tables` t WHERE t.store_id = s.id AND t.table_no = '1002');

INSERT INTO `tables` (store_id, table_no, table_name, qr_token, wechat_qr_url, alipay_qr_url, status)
SELECT s.id, '1003', '3号桌', '1003',
  'pages/index/index?token=1003&channel=wechat',
  'pages/index/index?token=1003&channel=alipay',
  1
FROM stores s
WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (SELECT 1 FROM `tables` t WHERE t.store_id = s.id AND t.table_no = '1003');

-- ============================================================================
-- 八、商品分类（挂总店）
-- ============================================================================
INSERT INTO product_categories (store_id, name, description, sort_order, status)
SELECT s.id, '招牌面', '本店招牌', 1, 1
FROM stores s WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (
    SELECT 1 FROM product_categories c WHERE c.store_id = s.id AND c.name = '招牌面'
  );

INSERT INTO product_categories (store_id, name, description, sort_order, status)
SELECT s.id, '小吃', '特色小吃', 2, 1
FROM stores s WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (
    SELECT 1 FROM product_categories c WHERE c.store_id = s.id AND c.name = '小吃'
  );

INSERT INTO product_categories (store_id, name, description, sort_order, status)
SELECT s.id, '饮品', '冷热饮品', 3, 1
FROM stores s WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (
    SELECT 1 FROM product_categories c WHERE c.store_id = s.id AND c.name = '饮品'
  );

-- ============================================================================
-- 九、规格组与选项（全局）
-- ============================================================================
INSERT INTO option_groups (name, code, selection_type, is_required, sort_order, status)
SELECT '面量', 'NOODLE_AMOUNT', 'SINGLE', 1, 1, 1 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM option_groups WHERE code = 'NOODLE_AMOUNT');

INSERT INTO option_groups (name, code, selection_type, is_required, sort_order, status)
SELECT '辣度', 'SPICY', 'SINGLE', 1, 2, 1 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM option_groups WHERE code = 'SPICY');

INSERT INTO option_groups (name, code, selection_type, is_required, sort_order, status)
SELECT '麻度', 'NUMB', 'SINGLE', 1, 3, 1 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM option_groups WHERE code = 'NUMB');

INSERT INTO option_groups (name, code, selection_type, is_required, sort_order, status)
SELECT '加料', 'EXTRA', 'MULTIPLE', 0, 4, 1 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM option_groups WHERE code = 'EXTRA');

-- 面量
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '少面', 'LESS', 0.00, 1, 1 FROM option_groups g WHERE g.code = 'NOODLE_AMOUNT'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'LESS');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '正常', 'NORMAL', 0.00, 2, 1 FROM option_groups g WHERE g.code = 'NOODLE_AMOUNT'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'NORMAL');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '多面', 'EXTRA', 1.00, 3, 1 FROM option_groups g WHERE g.code = 'NOODLE_AMOUNT'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'EXTRA');

-- 辣度
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '不要辣', 'NONE', 0.00, 1, 1 FROM option_groups g WHERE g.code = 'SPICY'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'NONE');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '少辣', 'LESS', 0.00, 2, 1 FROM option_groups g WHERE g.code = 'SPICY'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'LESS');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '正常辣', 'NORMAL', 0.00, 3, 1 FROM option_groups g WHERE g.code = 'SPICY'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'NORMAL');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '多辣', 'EXTRA', 0.00, 4, 1 FROM option_groups g WHERE g.code = 'SPICY'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'EXTRA');

-- 麻度
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '不要麻', 'NONE', 0.00, 1, 1 FROM option_groups g WHERE g.code = 'NUMB'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'NONE');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '少麻', 'LESS', 0.00, 2, 1 FROM option_groups g WHERE g.code = 'NUMB'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'LESS');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '正常麻', 'NORMAL', 0.00, 3, 1 FROM option_groups g WHERE g.code = 'NUMB'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'NORMAL');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '多麻', 'EXTRA', 0.00, 4, 1 FROM option_groups g WHERE g.code = 'NUMB'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'EXTRA');

-- 加料
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '加蛋', 'EGG', 2.00, 1, 1 FROM option_groups g WHERE g.code = 'EXTRA'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'EGG');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '加牛肉', 'BEEF', 5.00, 2, 1 FROM option_groups g WHERE g.code = 'EXTRA'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'BEEF');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '加肥肠', 'FEICHANG', 5.00, 3, 1 FROM option_groups g WHERE g.code = 'EXTRA'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'FEICHANG');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '加豆皮', 'BEAN_CURD', 2.00, 4, 1 FROM option_groups g WHERE g.code = 'EXTRA'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'BEAN_CURD');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '加香菜', 'CORIANDER', 0.00, 5, 1 FROM option_groups g WHERE g.code = 'EXTRA'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'CORIANDER');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '加葱', 'SCALLION', 0.00, 6, 1 FROM option_groups g WHERE g.code = 'EXTRA'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = 'SCALLION');

-- ============================================================================
-- 十、示例商品 + 规格绑定
-- ============================================================================
INSERT INTO products (
  store_id, category_id, product_no, name, description,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, sort_order
)
SELECT s.id, c.id, 'P001', '牛肉面', '经典牛肉面',
  18.00, 20.00, 999, 1.00, 1,
  'ON_SALE', 1, 1, 1
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '招牌面'
WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'P001');

INSERT INTO products (
  store_id, category_id, product_no, name, description,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, sort_order
)
SELECT s.id, c.id, 'P002', '酸辣粉', '地道酸辣粉',
  15.00, 16.00, 999, 1.00, 1,
  'ON_SALE', 1, 0, 2
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '招牌面'
WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'P002');

INSERT INTO products (
  store_id, category_id, product_no, name, description,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, sort_order
)
SELECT s.id, c.id, 'P003', '卤蛋', '入味卤蛋',
  3.00, 3.00, 999, 0.50, 1,
  'ON_SALE', 0, 0, 1
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '小吃'
WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'P003');

-- 牛肉面：面量+辣度+麻度+加料
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 1
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SNH001'
JOIN option_groups g ON g.code = 'NOODLE_AMOUNT'
WHERE p.product_no = 'P001'
  AND NOT EXISTS (
    SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id
  );

INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 2
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SNH001'
JOIN option_groups g ON g.code = 'SPICY'
WHERE p.product_no = 'P001'
  AND NOT EXISTS (
    SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id
  );

INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 3
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SNH001'
JOIN option_groups g ON g.code = 'NUMB'
WHERE p.product_no = 'P001'
  AND NOT EXISTS (
    SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id
  );

INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 4
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SNH001'
JOIN option_groups g ON g.code = 'EXTRA'
WHERE p.product_no = 'P001'
  AND NOT EXISTS (
    SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id
  );

-- 酸辣粉：辣度+麻度+加料
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 1
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SNH001'
JOIN option_groups g ON g.code = 'SPICY'
WHERE p.product_no = 'P002'
  AND NOT EXISTS (
    SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id
  );

INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 2
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SNH001'
JOIN option_groups g ON g.code = 'NUMB'
WHERE p.product_no = 'P002'
  AND NOT EXISTS (
    SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id
  );

INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 3
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SNH001'
JOIN option_groups g ON g.code = 'EXTRA'
WHERE p.product_no = 'P002'
  AND NOT EXISTS (
    SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id
  );

-- ============================================================================
-- 十一、系统业务参数（后台「系统设置」可在线改；启动时也会补缺）
-- ============================================================================
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

-- ============================================================================
-- 十二、测试顾客（微信 / 支付宝渠道 + 口味偏好）
-- ============================================================================
INSERT INTO customers (customer_no, display_name, nickname, phone, member_level_id, status, last_order_at)
SELECT 'SEED-C001', '测试微信顾客', '面友阿伟', '13800001001', ml.id, 1, NOW(3)
FROM member_levels ml
WHERE ml.name = '普通会员'
  AND NOT EXISTS (SELECT 1 FROM customers WHERE customer_no = 'SEED-C001');

INSERT INTO customers (customer_no, display_name, nickname, phone, member_level_id, status, last_order_at)
SELECT 'SEED-C002', '测试支付宝顾客', '面友小支', '13800001002', ml.id, 1, NOW(3)
FROM member_levels ml
WHERE ml.name = '银卡会员'
  AND NOT EXISTS (SELECT 1 FROM customers WHERE customer_no = 'SEED-C002');

INSERT INTO customer_wechat_accounts (customer_id, openid, unionid)
SELECT c.id, 'wx_dev_seed_c001', 'wx_union_seed_c001'
FROM customers c
WHERE c.customer_no = 'SEED-C001'
  AND NOT EXISTS (SELECT 1 FROM customer_wechat_accounts WHERE openid = 'wx_dev_seed_c001');

INSERT INTO customer_alipay_accounts (customer_id, alipay_user_id)
SELECT c.id, 'ali_dev_seed_c002'
FROM customers c
WHERE c.customer_no = 'SEED-C002'
  AND NOT EXISTS (SELECT 1 FROM customer_alipay_accounts WHERE alipay_user_id = 'ali_dev_seed_c002');

INSERT INTO customer_preferences (
  customer_id, category, option_code, option_name, preference_score, order_count, last_used_at
)
SELECT c.id, '辣度', 'NORMAL', '正常辣', 0.8200, 5, NOW(3)
FROM customers c
WHERE c.customer_no = 'SEED-C001'
  AND NOT EXISTS (
    SELECT 1 FROM customer_preferences p
    WHERE p.customer_id = c.id AND p.category = '辣度' AND p.option_code = 'NORMAL'
  );

INSERT INTO customer_preferences (
  customer_id, category, option_code, option_name, preference_score, order_count, last_used_at
)
SELECT c.id, '面量', 'NORMAL', '正常', 0.7600, 4, NOW(3)
FROM customers c
WHERE c.customer_no = 'SEED-C001'
  AND NOT EXISTS (
    SELECT 1 FROM customer_preferences p
    WHERE p.customer_id = c.id AND p.category = '面量' AND p.option_code = 'NORMAL'
  );

-- ============================================================================
-- 十三、测试订单 / 支付 / 退款（覆盖看板与支付页退款运维）
-- 场景：
--   SEED-PAY-PAID     已支付可退
--   SEED-PAY-PARTIAL  部分退款成功（含操作人）
--   SEED-PAY-FULL     全额退款成功
--   SEED-PAY-INFLIGHT 处理中退款（可测「同步」按钮；mock 环境 sync 可能无变化）
--   SEED-PAY-ALIPAY   支付宝已支付（渠道统计）
-- ============================================================================

-- 13.1 已支付可退 — 微信
INSERT INTO orders (
  order_no, customer_id, table_id, store_id, status, dining_type,
  subtotal, discount_amount, packing_fee, total_amount,
  payment_status, payment_method, channel, remark, ordered_at, paid_at
)
SELECT
  'SEED-ORD-PAID', c.id, t.id, s.id, 'PAID', 'DINE_IN',
  18.00, 0.00, 0.00, 18.00,
  'PAID', 'WECHAT', 'WECHAT', '种子：已支付可退', NOW(3), NOW(3)
FROM stores s
JOIN customers c ON c.customer_no = 'SEED-C001'
JOIN `tables` t ON t.store_id = s.id AND t.table_no = '1001'
WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (SELECT 1 FROM orders WHERE order_no = 'SEED-ORD-PAID');

INSERT INTO order_items (order_id, product_id, product_name_snapshot, unit_price_snapshot, quantity, subtotal)
SELECT o.id, p.id, '牛肉面', 18.00, 1, 18.00
FROM orders o
JOIN products p ON p.product_no = 'P001'
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SNH001'
WHERE o.order_no = 'SEED-ORD-PAID'
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id);

INSERT INTO payments (
  payment_no, order_id, customer_id, provider, channel, amount, currency, status,
  provider_trade_no, paid_at
)
SELECT
  'SEED-PAY-PAID', o.id, o.customer_id, 'WECHAT', 'JSAPI', 18.00, 'CNY', 'PAID',
  'wx_seed_trade_paid', o.paid_at
FROM orders o
WHERE o.order_no = 'SEED-ORD-PAID'
  AND NOT EXISTS (SELECT 1 FROM payments WHERE payment_no = 'SEED-PAY-PAID');

-- 13.2 部分退款 — 微信
INSERT INTO orders (
  order_no, customer_id, table_id, store_id, status, dining_type,
  subtotal, discount_amount, packing_fee, total_amount,
  payment_status, payment_method, channel, remark, ordered_at, paid_at
)
SELECT
  'SEED-ORD-PARTIAL', c.id, t.id, s.id, 'PAID', 'DINE_IN',
  36.00, 0.00, 0.00, 36.00,
  'PARTIAL_REFUND', 'WECHAT', 'WECHAT', '种子：部分退款', NOW(3), NOW(3)
FROM stores s
JOIN customers c ON c.customer_no = 'SEED-C001'
JOIN `tables` t ON t.store_id = s.id AND t.table_no = '1001'
WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (SELECT 1 FROM orders WHERE order_no = 'SEED-ORD-PARTIAL');

INSERT INTO order_items (order_id, product_id, product_name_snapshot, unit_price_snapshot, quantity, subtotal)
SELECT o.id, p.id, '牛肉面', 18.00, 2, 36.00
FROM orders o
JOIN products p ON p.product_no = 'P001'
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SNH001'
WHERE o.order_no = 'SEED-ORD-PARTIAL'
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id);

INSERT INTO payments (
  payment_no, order_id, customer_id, provider, channel, amount, currency, status,
  provider_trade_no, paid_at
)
SELECT
  'SEED-PAY-PARTIAL', o.id, o.customer_id, 'WECHAT', 'JSAPI', 36.00, 'CNY', 'PARTIAL_REFUND',
  'wx_seed_trade_partial', o.paid_at
FROM orders o
WHERE o.order_no = 'SEED-ORD-PARTIAL'
  AND NOT EXISTS (SELECT 1 FROM payments WHERE payment_no = 'SEED-PAY-PARTIAL');

INSERT INTO payment_refunds (
  refund_no, payment_id, order_id, amount, reason, status,
  provider_refund_no, fail_reason, succeeded_at, operator_id
)
SELECT
  'SEED-RF-PARTIAL', pay.id, o.id, 10.00, '少上一份卤蛋协商退款', 'SUCCESS',
  'wx_seed_rf_partial', NULL, NOW(3), u.id
FROM payments pay
JOIN orders o ON o.id = pay.order_id AND o.order_no = 'SEED-ORD-PARTIAL'
JOIN users u ON u.username = 'cashier'
WHERE pay.payment_no = 'SEED-PAY-PARTIAL'
  AND NOT EXISTS (SELECT 1 FROM payment_refunds WHERE refund_no = 'SEED-RF-PARTIAL');

-- 13.3 全额退款 — 微信
INSERT INTO orders (
  order_no, customer_id, table_id, store_id, status, dining_type,
  subtotal, discount_amount, packing_fee, total_amount,
  payment_status, payment_method, channel, remark, ordered_at, paid_at
)
SELECT
  'SEED-ORD-FULL', c.id, t.id, s.id, 'CANCELLED', 'DINE_IN',
  15.00, 0.00, 0.00, 15.00,
  'REFUNDED', 'WECHAT', 'WECHAT', '种子：全额退款', NOW(3), NOW(3)
FROM stores s
JOIN customers c ON c.customer_no = 'SEED-C001'
JOIN `tables` t ON t.store_id = s.id AND t.table_no = '1002'
WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (SELECT 1 FROM orders WHERE order_no = 'SEED-ORD-FULL');

INSERT INTO order_items (order_id, product_id, product_name_snapshot, unit_price_snapshot, quantity, subtotal)
SELECT o.id, p.id, '酸辣粉', 15.00, 1, 15.00
FROM orders o
JOIN products p ON p.product_no = 'P002'
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SNH001'
WHERE o.order_no = 'SEED-ORD-FULL'
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id);

INSERT INTO payments (
  payment_no, order_id, customer_id, provider, channel, amount, currency, status,
  provider_trade_no, paid_at
)
SELECT
  'SEED-PAY-FULL', o.id, o.customer_id, 'WECHAT', 'JSAPI', 15.00, 'CNY', 'REFUNDED',
  'wx_seed_trade_full', o.paid_at
FROM orders o
WHERE o.order_no = 'SEED-ORD-FULL'
  AND NOT EXISTS (SELECT 1 FROM payments WHERE payment_no = 'SEED-PAY-FULL');

INSERT INTO payment_refunds (
  refund_no, payment_id, order_id, amount, reason, status,
  provider_refund_no, fail_reason, succeeded_at, operator_id
)
SELECT
  'SEED-RF-FULL', pay.id, o.id, 15.00, '顾客取消全额退', 'SUCCESS',
  'wx_seed_rf_full', NULL, NOW(3), u.id
FROM payments pay
JOIN orders o ON o.id = pay.order_id AND o.order_no = 'SEED-ORD-FULL'
JOIN users u ON u.username = 'admin'
WHERE pay.payment_no = 'SEED-PAY-FULL'
  AND NOT EXISTS (SELECT 1 FROM payment_refunds WHERE refund_no = 'SEED-RF-FULL');

-- 13.4 处理中退款 — 微信（支付页「处理中退款 / 同步」）
INSERT INTO orders (
  order_no, customer_id, table_id, store_id, status, dining_type,
  subtotal, discount_amount, packing_fee, total_amount,
  payment_status, payment_method, channel, remark, ordered_at, paid_at
)
SELECT
  'SEED-ORD-INFLIGHT', c.id, t.id, s.id, 'PAID', 'DINE_IN',
  21.00, 0.00, 0.00, 21.00,
  'PAID', 'WECHAT', 'WECHAT', '种子：退款处理中', NOW(3), NOW(3)
FROM stores s
JOIN customers c ON c.customer_no = 'SEED-C001'
JOIN `tables` t ON t.store_id = s.id AND t.table_no = '1002'
WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (SELECT 1 FROM orders WHERE order_no = 'SEED-ORD-INFLIGHT');

INSERT INTO order_items (order_id, product_id, product_name_snapshot, unit_price_snapshot, quantity, subtotal)
SELECT o.id, p.id, '牛肉面', 18.00, 1, 18.00
FROM orders o
JOIN products p ON p.product_no = 'P001'
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SNH001'
WHERE o.order_no = 'SEED-ORD-INFLIGHT'
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id AND oi.product_name_snapshot = '牛肉面');

INSERT INTO order_items (order_id, product_id, product_name_snapshot, unit_price_snapshot, quantity, subtotal)
SELECT o.id, p.id, '卤蛋', 3.00, 1, 3.00
FROM orders o
JOIN products p ON p.product_no = 'P003'
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SNH001'
WHERE o.order_no = 'SEED-ORD-INFLIGHT'
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id AND oi.product_name_snapshot = '卤蛋');

INSERT INTO payments (
  payment_no, order_id, customer_id, provider, channel, amount, currency, status,
  provider_trade_no, paid_at
)
SELECT
  'SEED-PAY-INFLIGHT', o.id, o.customer_id, 'WECHAT', 'JSAPI', 21.00, 'CNY', 'PAID',
  'wx_seed_trade_inflight', o.paid_at
FROM orders o
WHERE o.order_no = 'SEED-ORD-INFLIGHT'
  AND NOT EXISTS (SELECT 1 FROM payments WHERE payment_no = 'SEED-PAY-INFLIGHT');

INSERT INTO payment_refunds (
  refund_no, payment_id, order_id, amount, reason, status,
  provider_refund_no, fail_reason, succeeded_at, operator_id
)
SELECT
  'SEED-RF-INFLIGHT', pay.id, o.id, 3.00, '卤蛋未出餐协商退', 'PROCESSING',
  NULL, NULL, NULL, u.id
FROM payments pay
JOIN orders o ON o.id = pay.order_id AND o.order_no = 'SEED-ORD-INFLIGHT'
JOIN users u ON u.username = 'cashier'
WHERE pay.payment_no = 'SEED-PAY-INFLIGHT'
  AND NOT EXISTS (SELECT 1 FROM payment_refunds WHERE refund_no = 'SEED-RF-INFLIGHT');

-- 13.5 支付宝已支付（数据概览渠道）
INSERT INTO orders (
  order_no, customer_id, table_id, store_id, status, dining_type,
  subtotal, discount_amount, packing_fee, total_amount,
  payment_status, payment_method, channel, remark, ordered_at, paid_at
)
SELECT
  'SEED-ORD-ALIPAY', c.id, t.id, s.id, 'PAID', 'DINE_IN',
  18.00, 0.00, 0.00, 18.00,
  'PAID', 'ALIPAY', 'ALIPAY', '种子：支付宝已支付', NOW(3), NOW(3)
FROM stores s
JOIN customers c ON c.customer_no = 'SEED-C002'
JOIN `tables` t ON t.store_id = s.id AND t.table_no = '1003'
WHERE s.store_no = 'SNH001'
  AND NOT EXISTS (SELECT 1 FROM orders WHERE order_no = 'SEED-ORD-ALIPAY');

INSERT INTO order_items (order_id, product_id, product_name_snapshot, unit_price_snapshot, quantity, subtotal)
SELECT o.id, p.id, '牛肉面', 18.00, 1, 18.00
FROM orders o
JOIN products p ON p.product_no = 'P001'
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SNH001'
WHERE o.order_no = 'SEED-ORD-ALIPAY'
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id);

INSERT INTO payments (
  payment_no, order_id, customer_id, provider, channel, amount, currency, status,
  provider_trade_no, paid_at
)
SELECT
  'SEED-PAY-ALIPAY', o.id, o.customer_id, 'ALIPAY', 'JSAPI', 18.00, 'CNY', 'PAID',
  'ali_seed_trade_paid', o.paid_at
FROM orders o
WHERE o.order_no = 'SEED-ORD-ALIPAY'
  AND NOT EXISTS (SELECT 1 FROM payments WHERE payment_no = 'SEED-PAY-ALIPAY');

-- 完成
SELECT 'seed_ok' AS result,
  (SELECT COUNT(*) FROM roles) AS roles,
  (SELECT COUNT(*) FROM permissions) AS permissions,
  (SELECT COUNT(*) FROM stores) AS stores,
  (SELECT COUNT(*) FROM `tables`) AS tables_cnt,
  (SELECT COUNT(*) FROM products) AS products,
  (SELECT COUNT(*) FROM customers) AS customers,
  (SELECT COUNT(*) FROM orders WHERE order_no LIKE 'SEED-%') AS seed_orders,
  (SELECT COUNT(*) FROM payments WHERE payment_no LIKE 'SEED-%') AS seed_payments,
  (SELECT COUNT(*) FROM payment_refunds WHERE refund_no LIKE 'SEED-%') AS seed_refunds;

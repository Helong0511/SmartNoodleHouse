-- ============================================================================
-- 智能面馆 SmartNoodleHouse — 测试 / 开发种子数据（**禁止用于生产**）
-- ============================================================================
-- 适用：本地开发、联调、自动化测试
-- 生产请使用：sql/002_seed_prod.sql + ADMIN_INITIAL_PASSWORD=强密码 python scripts/init_admin.py
-- 测试账号：admin / cashier / manager 均为 admin123（仅开发）
-- 退款：仅 admin(超管)、manager(店长) 可退款
-- 开发桌台扫码：见 tables.qr_token
-- 业务单号规则：业务2 + 门店编号(实际 store_no，固定4位) + 桌位3 + yyyyMMddHHmmss + 随机4（总长27）
--   OT堂食 / OD外卖 / PW微信付 / PA支付宝付 / RW微信退 / RA支付宝退 / CU顾客
-- 样例顾客：CU000000020260801100001S2C3 、 CU000000020260801100002S2C4
-- 样例订单/支付由 SQL CONCAT(业务, store_no, 桌位, 固定时间, 固定随机) 生成；门店段用实际 store_no
-- 例：OTSH0110020260801120101A2B3（SH01 + 桌1001→100）
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
SELECT 'CASHIER', '收银员', '订单、支付与收银点餐'
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

INSERT INTO permissions (code, name, module)
SELECT 'coupon:read', '查看优惠券', 'coupon' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'coupon:read');
INSERT INTO permissions (code, name, module)
SELECT 'coupon:manage', '管理优惠券', 'coupon' FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'coupon:manage');

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
  'customer:read', 'customer:manage',
  'camera:read', 'camera:manage',
  'welcome:manage',
  'payment:read', 'payment:refund',
  'table:manage',
  'user:read', 'user:manage',
  'coupon:read', 'coupon:manage'
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

-- 3.4 收银：订单 + 支付（不含退款，退款仅店长 STORE_ADMIN）
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
JOIN permissions p ON p.code IN (
  'order:read', 'order:update',
  'payment:read',
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

DELETE rp FROM role_permissions rp
JOIN roles r ON r.id = rp.role_id
JOIN permissions p ON p.id = rp.permission_id
WHERE r.code IN ('CASHIER', 'KITCHEN', 'OPERATOR') AND p.code = 'payment:refund';

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

INSERT INTO coupon_templates (name, coupon_type, discount_value, min_order_amount, valid_days)
SELECT '新客满30减5', 'FIXED', 5.00, 30.00, 30 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM coupon_templates WHERE name = '新客满30减5');

INSERT INTO coupon_templates (name, coupon_type, discount_value, min_order_amount, valid_days)
SELECT '满50享9折', 'PERCENT', 0.9000, 50.00, 14 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM coupon_templates WHERE name = '满50享9折');

INSERT INTO coupon_templates (name, coupon_type, discount_value, min_order_amount, valid_days)
SELECT '10元无门槛代金券', 'VOUCHER', 10.00, 0.00, 30 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM coupon_templates WHERE name = '10元无门槛代金券');

-- 五、门店（统一编号 SH01）
-- ============================================================================
INSERT INTO stores (store_no, name, address, phone, status)
SELECT 'SH01', '智能面馆太和总店', '太和大道100号', '400-001-0000', 1
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM stores WHERE store_no = 'SH01');

INSERT INTO stores (store_no, name, address, phone, status)
SELECT 'SH02', '智能面馆城南店', '太和大道88号', '400-002-0000', 1
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM stores WHERE store_no = 'SH02');

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

-- 门店收银（不可退款）
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
  AND s.store_no = 'SH01'
  AND NOT EXISTS (SELECT 1 FROM users WHERE username = 'cashier');

-- 门店店长（可退款，用于联调支付/外卖退款）
INSERT INTO users (username, password_hash, display_name, role_id, store_id, status)
SELECT
  'manager',
  '$2b$12$38dPQAo/7seG5MQ9uCaLg.eysxss7vHFFOYSORWyAO5sNflaFgaQm',
  '总店店长',
  r.id,
  s.id,
  1
FROM roles r
CROSS JOIN stores s
WHERE r.code = 'STORE_ADMIN'
  AND s.store_no = 'SH01'
  AND NOT EXISTS (SELECT 1 FROM users WHERE username = 'manager');

-- ============================================================================
-- 七、开发桌台（扫码 token = 桌号）
-- 小程序/H5：?token=1001 即可点餐；无桌台时下单会报「无效桌台二维码」
-- ============================================================================
INSERT INTO `tables` VALUES (1, 1, 'A01', '1号桌', 'cbqIjy3tT-ZaulwX9K0ICJ3LBwYTsO1H4MJPaooc3sE', 'weixin://dl/business/?appid=YOUR_WECHAT_APPID&path=pages/index/index&query=token%3DcbqIjy3tT-ZaulwX9K0ICJ3LBwYTsO1H4MJPaooc3sE%26channel%3Dwechat', 'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages/index/index&query=token%3DcbqIjy3tT-ZaulwX9K0ICJ3LBwYTsO1H4MJPaooc3sE%26channel%3Dalipay', 1, '2026-08-28 12:38:58.111', '2026-08-28 12:44:52.000');
INSERT INTO `tables` VALUES (2, 1, 'A02', '2号桌', 'J7aG8hvHA1lg2QUldWnYvV9yX9Y_C0y3ZLxVfZsHS-g', 'weixin://dl/business/?appid=YOUR_WECHAT_APPID&path=pages/index/index&query=token%3DJ7aG8hvHA1lg2QUldWnYvV9yX9Y_C0y3ZLxVfZsHS-g%26channel%3Dwechat', 'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages/index/index&query=token%3DJ7aG8hvHA1lg2QUldWnYvV9yX9Y_C0y3ZLxVfZsHS-g%26channel%3Dalipay', 1, '2026-08-28 12:38:58.117', '2026-08-28 12:44:53.000');
INSERT INTO `tables` VALUES (3, 1, 'W01', '外1号桌', 'k72g8pO1Df3vJOxQbTH2-O0e9rj76LTZ-jyq2UnixgI', 'weixin://dl/business/?appid=YOUR_WECHAT_APPID&path=pages/index/index&query=token%3Dk72g8pO1Df3vJOxQbTH2-O0e9rj76LTZ-jyq2UnixgI%26channel%3Dwechat', 'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages/index/index&query=token%3Dk72g8pO1Df3vJOxQbTH2-O0e9rj76LTZ-jyq2UnixgI%26channel%3Dalipay', 1, '2026-08-28 12:38:58.120', '2026-08-28 12:44:53.000');

-- ============================================================================
-- 八、商品分类（挂总店）
-- ============================================================================
INSERT INTO product_categories (store_id, category_no, name, description, sort_order, status)
SELECT s.id, 'SH01-ZhaoPaiMian', '招牌面', '本店招牌', 1, 1
FROM stores s WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1 FROM product_categories c WHERE c.store_id = s.id AND c.name = '招牌面'
  );

INSERT INTO product_categories (store_id, category_no, name, description, sort_order, status)
SELECT s.id, 'SH01-XiaoChi', '小吃', '特色小吃', 2, 1
FROM stores s WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1 FROM product_categories c WHERE c.store_id = s.id AND c.name = '小吃'
  );

INSERT INTO product_categories (store_id, category_no, name, description, sort_order, status)
SELECT s.id, 'SH01-YinPin', '饮品', '冷热饮品', 3, 1
FROM stores s WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1 FROM product_categories c WHERE c.store_id = s.id AND c.name = '饮品'
  );

-- ============================================================================
-- 九、规格组与选项（全局 / 总部前缀 0000）
-- ============================================================================
INSERT INTO option_groups (name, code, selection_type, is_required, sort_order, status)
SELECT '面量', '0000-NoodleAmount', 'SINGLE', 1, 1, 1 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM option_groups WHERE code = '0000-NoodleAmount');

INSERT INTO option_groups (name, code, selection_type, is_required, sort_order, status)
SELECT '辣度', '0000-Spicy', 'SINGLE', 1, 2, 1 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM option_groups WHERE code = '0000-Spicy');

INSERT INTO option_groups (name, code, selection_type, is_required, sort_order, status)
SELECT '麻度', '0000-Numb', 'SINGLE', 1, 3, 1 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM option_groups WHERE code = '0000-Numb');

INSERT INTO option_groups (name, code, selection_type, is_required, sort_order, status)
SELECT '加料', '0000-Extra', 'MULTIPLE', 0, 4, 1 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM option_groups WHERE code = '0000-Extra');

-- 面量
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '少面', '0000-NoodleAmount-Less', 0.00, 1, 1 FROM option_groups g WHERE g.code = '0000-NoodleAmount'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-NoodleAmount-Less');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '正常', '0000-NoodleAmount-Normal', 0.00, 2, 1 FROM option_groups g WHERE g.code = '0000-NoodleAmount'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-NoodleAmount-Normal');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '多面', '0000-NoodleAmount-More', 1.00, 3, 1 FROM option_groups g WHERE g.code = '0000-NoodleAmount'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-NoodleAmount-More');

-- 辣度
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '不要辣', '0000-Spicy-None', 0.00, 1, 1 FROM option_groups g WHERE g.code = '0000-Spicy'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Spicy-None');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '少辣', '0000-Spicy-Less', 0.00, 2, 1 FROM option_groups g WHERE g.code = '0000-Spicy'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Spicy-Less');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '正常辣', '0000-Spicy-Normal', 0.00, 3, 1 FROM option_groups g WHERE g.code = '0000-Spicy'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Spicy-Normal');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '多辣', '0000-Spicy-More', 0.00, 4, 1 FROM option_groups g WHERE g.code = '0000-Spicy'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Spicy-More');

-- 麻度
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '不要麻', '0000-Numb-None', 0.00, 1, 1 FROM option_groups g WHERE g.code = '0000-Numb'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Numb-None');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '少麻', '0000-Numb-Less', 0.00, 2, 1 FROM option_groups g WHERE g.code = '0000-Numb'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Numb-Less');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '正常麻', '0000-Numb-Normal', 0.00, 3, 1 FROM option_groups g WHERE g.code = '0000-Numb'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Numb-Normal');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '多麻', '0000-Numb-More', 0.00, 4, 1 FROM option_groups g WHERE g.code = '0000-Numb'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Numb-More');

-- 加料
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '加蛋', '0000-Extra-Egg', 2.00, 1, 1 FROM option_groups g WHERE g.code = '0000-Extra'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Extra-Egg');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '加牛肉', '0000-Extra-Beef', 5.00, 2, 1 FROM option_groups g WHERE g.code = '0000-Extra'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Extra-Beef');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '加肥肠', '0000-Extra-FeiChang', 5.00, 3, 1 FROM option_groups g WHERE g.code = '0000-Extra'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Extra-FeiChang');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '加豆皮', '0000-Extra-BeanCurd', 2.00, 4, 1 FROM option_groups g WHERE g.code = '0000-Extra'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Extra-BeanCurd');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '加香菜', '0000-Extra-Coriander', 0.00, 5, 1 FROM option_groups g WHERE g.code = '0000-Extra'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Extra-Coriander');
INSERT INTO options (group_id, name, code, extra_price, sort_order, status)
SELECT g.id, '加葱', '0000-Extra-Scallion', 0.00, 6, 1 FROM option_groups g WHERE g.code = '0000-Extra'
  AND NOT EXISTS (SELECT 1 FROM options o WHERE o.group_id = g.id AND o.code = '0000-Extra-Scallion');

-- ============================================================================
-- 十、示例商品 + 规格绑定
-- ============================================================================
INSERT INTO products (
  store_id, category_id, product_no, name, description,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, sort_order
)
SELECT s.id, c.id, 'SH01-ZhaoPaiMian-NiuRouMian', '牛肉面', '经典牛肉面',
  18.00, 20.00, 999, 1.00, 1,
  'ON_SALE', 1, 1, 1
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '招牌面'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-ZhaoPaiMian-NiuRouMian');

INSERT INTO products (
  store_id, category_id, product_no, name, description,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, sort_order
)
SELECT s.id, c.id, 'SH01-ZhaoPaiMian-SuanLaFen', '酸辣粉', '地道酸辣粉',
  15.00, 16.00, 999, 1.00, 1,
  'ON_SALE', 1, 0, 2
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '招牌面'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-ZhaoPaiMian-SuanLaFen');

INSERT INTO products (
  store_id, category_id, product_no, name, description,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, sort_order
)
SELECT s.id, c.id, 'SH01-XiaoChi-LuDan', '卤蛋', '入味卤蛋',
  3.00, 3.00, 999, 0.50, 1,
  'ON_SALE', 0, 0, 1
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '小吃'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-XiaoChi-LuDan');

-- 牛肉面：面量+辣度+麻度+加料
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 1
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SH01'
JOIN option_groups g ON g.code = '0000-NoodleAmount'
WHERE p.product_no = 'SH01-ZhaoPaiMian-NiuRouMian'
  AND NOT EXISTS (
    SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id
  );

INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 2
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SH01'
JOIN option_groups g ON g.code = '0000-Spicy'
WHERE p.product_no = 'SH01-ZhaoPaiMian-NiuRouMian'
  AND NOT EXISTS (
    SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id
  );

INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 3
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SH01'
JOIN option_groups g ON g.code = '0000-Numb'
WHERE p.product_no = 'SH01-ZhaoPaiMian-NiuRouMian'
  AND NOT EXISTS (
    SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id
  );

INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 4
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SH01'
JOIN option_groups g ON g.code = '0000-Extra'
WHERE p.product_no = 'SH01-ZhaoPaiMian-NiuRouMian'
  AND NOT EXISTS (
    SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id
  );

-- 酸辣粉：辣度+麻度+加料
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 1
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SH01'
JOIN option_groups g ON g.code = '0000-Spicy'
WHERE p.product_no = 'SH01-ZhaoPaiMian-SuanLaFen'
  AND NOT EXISTS (
    SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id
  );

INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 2
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SH01'
JOIN option_groups g ON g.code = '0000-Numb'
WHERE p.product_no = 'SH01-ZhaoPaiMian-SuanLaFen'
  AND NOT EXISTS (
    SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id
  );

INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, 3
FROM products p
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SH01'
JOIN option_groups g ON g.code = '0000-Extra'
WHERE p.product_no = 'SH01-ZhaoPaiMian-SuanLaFen'
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

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'kitchen_auto_complete_minutes', '20', '支付后厨房 KDS 未处理超过该分钟数则自动完成出餐；0=关闭'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'kitchen_auto_complete_minutes');

-- ============================================================================
-- 十二、测试顾客（微信 / 支付宝渠道 + 口味偏好）
-- 单号：CU + 0000 + 000 + 时间 + 随机4
-- ============================================================================

-- 兼容旧种子顾客号
UPDATE customers SET customer_no = 'CU000000020260801100001S2C3' WHERE customer_no = 'SEED-C001';
UPDATE customers SET customer_no = 'CU000000020260801100002S2C4' WHERE customer_no = 'SEED-C002';

INSERT INTO customers (customer_no, display_name, nickname, phone, member_level_id, status, last_order_at)
SELECT 'CU000000020260801100001S2C3', '测试微信顾客', '面友阿伟', '13800001001', ml.id, 1, NOW(3)
FROM member_levels ml
WHERE ml.name = '普通会员'
  AND NOT EXISTS (SELECT 1 FROM customers WHERE customer_no = 'CU000000020260801100001S2C3');

INSERT INTO customers (customer_no, display_name, nickname, phone, member_level_id, status, last_order_at)
SELECT 'CU000000020260801100002S2C4', '测试支付宝顾客', '面友小支', '13800001002', ml.id, 1, NOW(3)
FROM member_levels ml
WHERE ml.name = '银卡会员'
  AND NOT EXISTS (SELECT 1 FROM customers WHERE customer_no = 'CU000000020260801100002S2C4');

-- 联调顾客视为在总店 1 号桌扫码注册（方便后台顾客管理核对注册门店）
UPDATE customers c
JOIN stores s ON s.store_no = 'SH01'
JOIN `tables` t ON t.store_id = s.id AND t.table_no = 'A01'
SET c.registered_store_id = s.id, c.registered_table_id = t.id
WHERE c.customer_no IN ('CU000000020260801100001S2C3', 'CU000000020260801100002S2C4')
  AND c.registered_store_id IS NULL;

INSERT INTO customer_wechat_accounts (customer_id, openid, unionid, profile_completed, nickname)
SELECT c.id, 'wx_dev_seed_c001', 'wx_union_seed_c001', 1, c.nickname
FROM customers c
WHERE c.customer_no = 'CU000000020260801100001S2C3'
  AND NOT EXISTS (SELECT 1 FROM customer_wechat_accounts WHERE openid = 'wx_dev_seed_c001');

INSERT INTO customer_coupons (customer_id, template_id, coupon_no, status, expires_at)
SELECT c.id, t.id, 'CP000000020260801100001S2C3', 'AVAILABLE', DATE_ADD(NOW(3), INTERVAL 30 DAY)
FROM customers c
JOIN coupon_templates t ON t.name = '新客满30减5'
WHERE c.customer_no = 'CU000000020260801100001S2C3'
  AND NOT EXISTS (SELECT 1 FROM customer_coupons WHERE coupon_no = 'CP000000020260801100001S2C3');

INSERT INTO customer_coupons (customer_id, template_id, coupon_no, status, expires_at)
SELECT c.id, t.id, 'VP000000020260801100001S2C3', 'AVAILABLE', DATE_ADD(NOW(3), INTERVAL 30 DAY)
FROM customers c
JOIN coupon_templates t ON t.name = '10元无门槛代金券'
WHERE c.customer_no = 'CU000000020260801100001S2C3'
  AND NOT EXISTS (SELECT 1 FROM customer_coupons WHERE coupon_no = 'VP000000020260801100001S2C3');

INSERT INTO customer_alipay_accounts (customer_id, alipay_user_id, profile_completed, nickname)
SELECT c.id, 'ali_dev_seed_c002', 1, c.nickname
FROM customers c
WHERE c.customer_no = 'CU000000020260801100002S2C4'
  AND NOT EXISTS (SELECT 1 FROM customer_alipay_accounts WHERE alipay_user_id = 'ali_dev_seed_c002');

INSERT INTO customer_preferences (
  customer_id, category, option_code, option_name, preference_score, order_count, last_used_at
)
SELECT c.id, '辣度', 'NORMAL', '正常辣', 0.8200, 5, NOW(3)
FROM customers c
WHERE c.customer_no = 'CU000000020260801100001S2C3'
  AND NOT EXISTS (
    SELECT 1 FROM customer_preferences p
    WHERE p.customer_id = c.id AND p.category = '辣度' AND p.option_code = 'NORMAL'
  );

INSERT INTO customer_preferences (
  customer_id, category, option_code, option_name, preference_score, order_count, last_used_at
)
SELECT c.id, '面量', 'NORMAL', '正常', 0.7600, 4, NOW(3)
FROM customers c
WHERE c.customer_no = 'CU000000020260801100001S2C3'
  AND NOT EXISTS (
    SELECT 1 FROM customer_preferences p
    WHERE p.customer_id = c.id AND p.category = '面量' AND p.option_code = 'NORMAL'
  );

INSERT INTO customer_preferences (
  customer_id, category, option_code, option_name, preference_score, order_count, last_used_at
)
SELECT c.id, '加料', 'SCALLION', '加葱', 0.6500, 4, NOW(3)
FROM customers c
WHERE c.customer_no = 'CU000000020260801100001S2C3'
  AND NOT EXISTS (
    SELECT 1 FROM customer_preferences p
    WHERE p.customer_id = c.id AND p.category = '加料' AND p.option_code = 'SCALLION'
  );

INSERT INTO customer_preferences (
  customer_id, category, option_code, option_name, preference_score, order_count, last_used_at
)
SELECT c.id, '加料', 'CORIANDER', '加香菜', 0.5800, 3, NOW(3)
FROM customers c
WHERE c.customer_no = 'CU000000020260801100001S2C3'
  AND NOT EXISTS (
    SELECT 1 FROM customer_preferences p
    WHERE p.customer_id = c.id AND p.category = '加料' AND p.option_code = 'CORIANDER'
  );

INSERT INTO customer_preferences (
  customer_id, category, option_code, option_name, preference_score, order_count, last_used_at
)
SELECT c.id, '加料', 'FEICHANG', '加肥肠', 0.5200, 3, NOW(3)
FROM customers c
WHERE c.customer_no = 'CU000000020260801100002S2C4'
  AND NOT EXISTS (
    SELECT 1 FROM customer_preferences p
    WHERE p.customer_id = c.id AND p.category = '加料' AND p.option_code = 'FEICHANG'
  );

-- ============================================================================
-- 十三、测试订单 / 支付 / 退款（覆盖看板与支付页退款运维）
-- 单号：{OT|PW|PA|RW}{store_no}{桌位3}{yyyyMMddHHmmss}{随机4}；门店用实际编号如 SH01；桌号1001→100
-- 场景备注仍含「种子：」便于识别；先清理旧 SEED-* 测试单避免并存
-- ============================================================================

DELETE pr FROM payment_refunds pr
JOIN payments pay ON pay.id = pr.payment_id
JOIN orders o ON o.id = pay.order_id
WHERE o.order_no LIKE 'SEED-%' OR pay.payment_no LIKE 'SEED-%' OR pr.refund_no LIKE 'SEED-%';

DELETE pay FROM payments pay
JOIN orders o ON o.id = pay.order_id
WHERE o.order_no LIKE 'SEED-%' OR pay.payment_no LIKE 'SEED-%';

DELETE FROM orders WHERE order_no LIKE 'SEED-%';

-- 13.1 已支付可退 — 微信 / 桌 1001
INSERT INTO orders (
  order_no, customer_id, table_id, store_id, status, dining_type,
  subtotal, discount_amount, packing_fee, total_amount,
  payment_status, payment_method, channel, remark, ordered_at, paid_at
)
SELECT
  CONCAT('OT', s.store_no, '100', '20260801120101', 'A2B3'),
  c.id, t.id, s.id, 'PAID', 'DINE_IN',
  18.00, 0.00, 0.00, 18.00,
  'PAID', 'WECHAT', 'WECHAT', '种子：已支付可退', NOW(3), NOW(3)
FROM stores s
JOIN customers c ON c.customer_no = 'CU000000020260801100001S2C3'
JOIN `tables` t ON t.store_id = s.id AND t.table_no = '1001'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1 FROM orders o2
    WHERE o2.order_no = CONCAT('OT', s.store_no, '100', '20260801120101', 'A2B3')
  );

INSERT INTO order_items (order_id, product_id, product_name_snapshot, unit_price_snapshot, quantity, subtotal)
SELECT o.id, p.id, '牛肉面', 18.00, 1, 18.00
FROM orders o
JOIN products p ON p.product_no = 'SH01-ZhaoPaiMian-NiuRouMian'
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SH01' AND s.id = o.store_id
WHERE o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120101', 'A2B3')
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id);

INSERT INTO payments (
  payment_no, order_id, customer_id, provider, channel, amount, currency, status,
  provider_trade_no, paid_at
)
SELECT
  CONCAT('PW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120102', 'C4D5'),
  o.id, o.customer_id, 'WECHAT', 'JSAPI', 18.00, 'CNY', 'PAID',
  'wx_seed_trade_paid', o.paid_at
FROM orders o
WHERE o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120101', 'A2B3')
  AND NOT EXISTS (
    SELECT 1 FROM payments pay
    WHERE pay.payment_no = CONCAT('PW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120102', 'C4D5')
  );

-- 13.2 部分退款 — 微信 / 桌 1001
INSERT INTO orders (
  order_no, customer_id, table_id, store_id, status, dining_type,
  subtotal, discount_amount, packing_fee, total_amount,
  payment_status, payment_method, channel, remark, ordered_at, paid_at
)
SELECT
  CONCAT('OT', s.store_no, '100', '20260801120201', 'E6F7'),
  c.id, t.id, s.id, 'PAID', 'DINE_IN',
  36.00, 0.00, 0.00, 36.00,
  'PARTIAL_REFUND', 'WECHAT', 'WECHAT', '种子：部分退款', NOW(3), NOW(3)
FROM stores s
JOIN customers c ON c.customer_no = 'CU000000020260801100001S2C3'
JOIN `tables` t ON t.store_id = s.id AND t.table_no = '1001'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1 FROM orders o2
    WHERE o2.order_no = CONCAT('OT', s.store_no, '100', '20260801120201', 'E6F7')
  );

INSERT INTO order_items (order_id, product_id, product_name_snapshot, unit_price_snapshot, quantity, subtotal)
SELECT o.id, p.id, '牛肉面', 18.00, 2, 36.00
FROM orders o
JOIN products p ON p.product_no = 'SH01-ZhaoPaiMian-NiuRouMian'
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SH01' AND s.id = o.store_id
WHERE o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120201', 'E6F7')
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id);

INSERT INTO payments (
  payment_no, order_id, customer_id, provider, channel, amount, currency, status,
  provider_trade_no, paid_at
)
SELECT
  CONCAT('PW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120202', 'G8H9'),
  o.id, o.customer_id, 'WECHAT', 'JSAPI', 36.00, 'CNY', 'PARTIAL_REFUND',
  'wx_seed_trade_partial', o.paid_at
FROM orders o
WHERE o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120201', 'E6F7')
  AND NOT EXISTS (
    SELECT 1 FROM payments pay
    WHERE pay.payment_no = CONCAT('PW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120202', 'G8H9')
  );

INSERT INTO payment_refunds (
  refund_no, payment_id, order_id, amount, reason, status,
  provider_refund_no, fail_reason, succeeded_at, operator_id
)
SELECT
  CONCAT('RW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120300', 'J2K3'),
  pay.id, o.id, 10.00, '少上一份卤蛋协商退款', 'SUCCESS',
  'wx_seed_rf_partial', NULL, NOW(3), u.id
FROM payments pay
JOIN orders o ON o.id = pay.order_id
  AND o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120201', 'E6F7')
JOIN users u ON u.username = 'cashier'
WHERE pay.payment_no = CONCAT('PW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120202', 'G8H9')
  AND NOT EXISTS (
    SELECT 1 FROM payment_refunds r
    WHERE r.refund_no = CONCAT('RW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120300', 'J2K3')
  );

-- 13.3 全额退款 — 微信 / 桌 1002
INSERT INTO orders (
  order_no, customer_id, table_id, store_id, status, dining_type,
  subtotal, discount_amount, packing_fee, total_amount,
  payment_status, payment_method, channel, remark, ordered_at, paid_at
)
SELECT
  CONCAT('OT', s.store_no, '100', '20260801120401', 'L4M5'),
  c.id, t.id, s.id, 'CANCELLED', 'DINE_IN',
  15.00, 0.00, 0.00, 15.00,
  'REFUNDED', 'WECHAT', 'WECHAT', '种子：全额退款', NOW(3), NOW(3)
FROM stores s
JOIN customers c ON c.customer_no = 'CU000000020260801100001S2C3'
JOIN `tables` t ON t.store_id = s.id AND t.table_no = '1002'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1 FROM orders o2
    WHERE o2.order_no = CONCAT('OT', s.store_no, '100', '20260801120401', 'L4M5')
  );

INSERT INTO order_items (order_id, product_id, product_name_snapshot, unit_price_snapshot, quantity, subtotal)
SELECT o.id, p.id, '酸辣粉', 15.00, 1, 15.00
FROM orders o
JOIN products p ON p.product_no = 'SH01-ZhaoPaiMian-SuanLaFen'
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SH01' AND s.id = o.store_id
WHERE o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120401', 'L4M5')
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id);

INSERT INTO payments (
  payment_no, order_id, customer_id, provider, channel, amount, currency, status,
  provider_trade_no, paid_at
)
SELECT
  CONCAT('PW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120402', 'N6P7'),
  o.id, o.customer_id, 'WECHAT', 'JSAPI', 15.00, 'CNY', 'REFUNDED',
  'wx_seed_trade_full', o.paid_at
FROM orders o
WHERE o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120401', 'L4M5')
  AND NOT EXISTS (
    SELECT 1 FROM payments pay
    WHERE pay.payment_no = CONCAT('PW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120402', 'N6P7')
  );

INSERT INTO payment_refunds (
  refund_no, payment_id, order_id, amount, reason, status,
  provider_refund_no, fail_reason, succeeded_at, operator_id
)
SELECT
  CONCAT('RW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120500', 'Q8R9'),
  pay.id, o.id, 15.00, '顾客取消全额退', 'SUCCESS',
  'wx_seed_rf_full', NULL, NOW(3), u.id
FROM payments pay
JOIN orders o ON o.id = pay.order_id
  AND o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120401', 'L4M5')
JOIN users u ON u.username = 'admin'
WHERE pay.payment_no = CONCAT('PW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120402', 'N6P7')
  AND NOT EXISTS (
    SELECT 1 FROM payment_refunds r
    WHERE r.refund_no = CONCAT('RW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120500', 'Q8R9')
  );

-- 13.4 处理中退款 — 微信 / 桌 1002
INSERT INTO orders (
  order_no, customer_id, table_id, store_id, status, dining_type,
  subtotal, discount_amount, packing_fee, total_amount,
  payment_status, payment_method, channel, remark, ordered_at, paid_at
)
SELECT
  CONCAT('OT', s.store_no, '100', '20260801120601', 'S2T3'),
  c.id, t.id, s.id, 'PAID', 'DINE_IN',
  21.00, 0.00, 0.00, 21.00,
  'PAID', 'WECHAT', 'WECHAT', '种子：退款处理中', NOW(3), NOW(3)
FROM stores s
JOIN customers c ON c.customer_no = 'CU000000020260801100001S2C3'
JOIN `tables` t ON t.store_id = s.id AND t.table_no = '1002'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1 FROM orders o2
    WHERE o2.order_no = CONCAT('OT', s.store_no, '100', '20260801120601', 'S2T3')
  );

INSERT INTO order_items (order_id, product_id, product_name_snapshot, unit_price_snapshot, quantity, subtotal)
SELECT o.id, p.id, '牛肉面', 18.00, 1, 18.00
FROM orders o
JOIN products p ON p.product_no = 'SH01-ZhaoPaiMian-NiuRouMian'
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SH01' AND s.id = o.store_id
WHERE o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120601', 'S2T3')
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id AND oi.product_name_snapshot = '牛肉面');

INSERT INTO order_items (order_id, product_id, product_name_snapshot, unit_price_snapshot, quantity, subtotal)
SELECT o.id, p.id, '卤蛋', 3.00, 1, 3.00
FROM orders o
JOIN products p ON p.product_no = 'SH01-XiaoChi-LuDan'
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SH01' AND s.id = o.store_id
WHERE o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120601', 'S2T3')
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id AND oi.product_name_snapshot = '卤蛋');

INSERT INTO payments (
  payment_no, order_id, customer_id, provider, channel, amount, currency, status,
  provider_trade_no, paid_at
)
SELECT
  CONCAT('PW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120602', 'U4V5'),
  o.id, o.customer_id, 'WECHAT', 'JSAPI', 21.00, 'CNY', 'PAID',
  'wx_seed_trade_inflight', o.paid_at
FROM orders o
WHERE o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120601', 'S2T3')
  AND NOT EXISTS (
    SELECT 1 FROM payments pay
    WHERE pay.payment_no = CONCAT('PW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120602', 'U4V5')
  );

INSERT INTO payment_refunds (
  refund_no, payment_id, order_id, amount, reason, status,
  provider_refund_no, fail_reason, succeeded_at, operator_id
)
SELECT
  CONCAT('RW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120700', 'W6X7'),
  pay.id, o.id, 3.00, '卤蛋未出餐协商退', 'PROCESSING',
  NULL, NULL, NULL, u.id
FROM payments pay
JOIN orders o ON o.id = pay.order_id
  AND o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120601', 'S2T3')
JOIN users u ON u.username = 'cashier'
WHERE pay.payment_no = CONCAT('PW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120602', 'U4V5')
  AND NOT EXISTS (
    SELECT 1 FROM payment_refunds r
    WHERE r.refund_no = CONCAT('RW', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120700', 'W6X7')
  );

-- 13.5 支付宝已支付 — 桌 1003
INSERT INTO orders (
  order_no, customer_id, table_id, store_id, status, dining_type,
  subtotal, discount_amount, packing_fee, total_amount,
  payment_status, payment_method, channel, remark, ordered_at, paid_at
)
SELECT
  CONCAT('OT', s.store_no, '100', '20260801120801', 'Y8Z2'),
  c.id, t.id, s.id, 'PAID', 'DINE_IN',
  18.00, 0.00, 0.00, 18.00,
  'PAID', 'ALIPAY', 'ALIPAY', '种子：支付宝已支付', NOW(3), NOW(3)
FROM stores s
JOIN customers c ON c.customer_no = 'CU000000020260801100002S2C4'
JOIN `tables` t ON t.store_id = s.id AND t.table_no = '1003'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1 FROM orders o2
    WHERE o2.order_no = CONCAT('OT', s.store_no, '100', '20260801120801', 'Y8Z2')
  );

INSERT INTO order_items (order_id, product_id, product_name_snapshot, unit_price_snapshot, quantity, subtotal)
SELECT o.id, p.id, '牛肉面', 18.00, 1, 18.00
FROM orders o
JOIN products p ON p.product_no = 'SH01-ZhaoPaiMian-NiuRouMian'
JOIN stores s ON s.id = p.store_id AND s.store_no = 'SH01' AND s.id = o.store_id
WHERE o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120801', 'Y8Z2')
  AND NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id);

INSERT INTO payments (
  payment_no, order_id, customer_id, provider, channel, amount, currency, status,
  provider_trade_no, paid_at
)
SELECT
  CONCAT('PA', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120802', 'A3B4'),
  o.id, o.customer_id, 'ALIPAY', 'JSAPI', 18.00, 'CNY', 'PAID',
  'ali_seed_trade_paid', o.paid_at
FROM orders o
WHERE o.order_no = CONCAT('OT', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120801', 'Y8Z2')
  AND NOT EXISTS (
    SELECT 1 FROM payments pay
    WHERE pay.payment_no = CONCAT('PA', (SELECT store_no FROM stores WHERE id = o.store_id), '100', '20260801120802', 'A3B4')
  );

-- 完成
SELECT 'seed_ok' AS result,
  (SELECT COUNT(*) FROM roles) AS roles,
  (SELECT COUNT(*) FROM permissions) AS permissions,
  (SELECT COUNT(*) FROM stores) AS stores,
  (SELECT COUNT(*) FROM `tables`) AS tables_cnt,
  (SELECT COUNT(*) FROM products) AS products,
  (SELECT COUNT(*) FROM customers) AS customers,
  (SELECT COUNT(*) FROM orders WHERE remark LIKE '种子：%') AS seed_orders,
  (SELECT COUNT(*) FROM payments pay
     JOIN orders o ON o.id = pay.order_id
    WHERE o.remark LIKE '种子：%') AS seed_payments,
  (SELECT COUNT(*) FROM payment_refunds r
     JOIN orders o ON o.id = r.order_id
    WHERE o.remark LIKE '种子：%') AS seed_refunds;

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'preference_scene_enabled', 'true', '口味场景加权（季节/同星期同餐段）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'preference_scene_enabled');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'weather_pref_enabled', 'true', '天气口味加权（需门店经纬度）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'weather_pref_enabled');

-- 初始种子数据
USE smart_noodle_house;

INSERT INTO roles (code, name, description) VALUES
('SUPER_ADMIN', '超级管理员', '全部权限'),
('STORE_ADMIN', '门店管理员', '门店管理'),
('CASHIER', '收银员', '订单与支付'),
('KITCHEN', '厨房', 'KDS出餐'),
('OPERATOR', '运营', '商品与数据');

INSERT INTO permissions (code, name, module) VALUES
('product:read', '查看商品', 'product'),
('product:create', '创建商品', 'product'),
('product:update', '更新商品', 'product'),
('product:delete', '删除商品', 'product'),
('order:read', '查看订单', 'order'),
('order:update', '更新订单', 'order'),
('customer:read', '查看顾客', 'customer'),
('camera:read', '查看摄像头', 'camera'),
('camera:manage', '管理摄像头', 'camera'),
('welcome:manage', '管理欢迎', 'welcome'),
('payment:read', '查看支付', 'payment'),
('payment:refund', '支付退款', 'payment'),
('system:manage', '系统管理', 'system');

INSERT INTO role_permissions (role_id, permission_id)
SELECT 1, id FROM permissions;

INSERT INTO member_levels (name, min_points, discount_rate) VALUES
('普通会员', 0, 1.0000),
('银卡会员', 100, 0.9800),
('金卡会员', 500, 0.9500);

INSERT INTO stores (store_no, name, address, phone) VALUES
('SNH001', '智能面馆总店', '示例地址', '400-000-0000');

-- 默认管理员 admin/admin123 (bcrypt hash placeholder, 应用启动时会重置)
INSERT INTO users (username, password_hash, display_name, role_id) VALUES
('admin', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5GyYqJqZqZqZq', '系统管理员', 1);

INSERT INTO product_categories (name, description, sort_order) VALUES
('招牌面', '本店招牌', 1),
('小吃', '特色小吃', 2),
('饮品', '冷热饮品', 3);

INSERT INTO option_groups (name, code, selection_type, is_required, sort_order) VALUES
('面量', 'NOODLE_AMOUNT', 'SINGLE', 1, 1),
('辣度', 'SPICY', 'SINGLE', 1, 2),
('麻度', 'NUMB', 'SINGLE', 1, 3),
('加料', 'EXTRA', 'MULTIPLE', 0, 4);

INSERT INTO options (group_id, name, code, extra_price, sort_order) VALUES
(1, '少面', 'LESS', 0.00, 1),
(1, '正常', 'NORMAL', 0.00, 2),
(1, '多面', 'EXTRA', 2.00, 3),
(2, '不要辣', 'NONE', 0.00, 1),
(2, '少辣', 'LESS', 0.00, 2),
(2, '正常辣', 'NORMAL', 0.00, 3),
(2, '多辣', 'EXTRA', 0.00, 4),
(3, '不要麻', 'NONE', 0.00, 1),
(3, '少麻', 'LESS', 0.00, 2),
(3, '正常麻', 'NORMAL', 0.00, 3),
(3, '多麻', 'EXTRA', 0.00, 4),
(4, '加蛋', 'EGG', 2.00, 1),
(4, '加牛肉', 'BEEF', 5.00, 2),
(4, '加豆皮', 'BEAN_CURD', 2.00, 3),
(4, '加香菜', 'CORIANDER', 0.00, 4),
(4, '加葱', 'SCALLION', 0.00, 5);

INSERT INTO products (category_id, product_no, name, description, price, original_price, status, is_recommended, is_hot, sort_order) VALUES
(1, 'P001', '牛肉面', '经典牛肉面', 18.00, 20.00, 'ON_SALE', 1, 1, 1),
(1, 'P002', '酸辣粉', '地道酸辣粉', 15.00, 16.00, 'ON_SALE', 1, 0, 2),
(2, 'P003', '卤蛋', '入味卤蛋', 3.00, 3.00, 'ON_SALE', 0, 0, 1);

INSERT INTO product_option_groups (product_id, group_id, sort_order) VALUES
(1, 1, 1), (1, 2, 2), (1, 3, 3), (1, 4, 4),
(2, 2, 1), (2, 3, 2), (2, 4, 3);

INSERT INTO system_settings (setting_key, setting_value, description) VALUES
('order_payment_timeout_minutes', '15', '订单支付超时分钟数'),
('visitor_data_retention_days', '7', '匿名访客数据保留天数'),
('welcome_cooldown_seconds', '60', '欢迎冷却秒数'),
('face_match_threshold', '0.75', '人脸匹配阈值'),
('face_confirm_frames', '3', '人脸确认帧数');

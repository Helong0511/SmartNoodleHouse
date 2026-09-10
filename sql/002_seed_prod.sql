-- ============================================================================
-- 智能面馆 — 生产环境种子数据
-- ============================================================================
-- 前置：sql/001_schema.sql
-- 后续：python scripts/init_admin.py（如需重置超管密码；本文件已含 admin/hsf/syxm/cwm）
-- 多门店：第六节按实际增删 store_no
--
-- ⚠ 会先清空 001_schema.sql 中全部业务表数据，再写入种子（不可对线上有业务数据的库随意执行）
--
-- 退款权限策略：
--   1) 收银员 CASHIER：无 payment:refund；收银渠道须「本班订单申请 → 店长审批」
--   2) 店长 STORE_ADMIN / 超管 SUPER_ADMIN：持有 payment:refund，可管理端直连或走 POS 审批页
--   3) 后厨/运营：不得持有 payment:refund（见文末 DELETE 校正）
-- ============================================================================

SET NAMES utf8mb4;
USE smart_noodle_house;

-- ---------------------------------------------------------------------------
-- 〇、清空全部业务表（对齐 sql/001_schema.sql；TRUNCATE 同时重置自增）
-- ---------------------------------------------------------------------------
SET FOREIGN_KEY_CHECKS = 0;

TRUNCATE TABLE welcome_logs;
TRUNCATE TABLE face_profiles;
TRUNCATE TABLE visitor_events;
TRUNCATE TABLE cameras;
TRUNCATE TABLE delivery_callback_logs;
TRUNCATE TABLE delivery_refunds;
TRUNCATE TABLE platform_store_bindings;
TRUNCATE TABLE delivery_orders;
TRUNCATE TABLE delivery_product_maps;
TRUNCATE TABLE point_ledger;
TRUNCATE TABLE financial_ledger;
TRUNCATE TABLE webhook_inbox;
TRUNCATE TABLE payment_callback_receipts;
TRUNCATE TABLE payment_status_history;
TRUNCATE TABLE pos_refund_requests;
TRUNCATE TABLE cashier_shifts;
TRUNCATE TABLE payment_refunds;
TRUNCATE TABLE payments;
TRUNCATE TABLE order_status_history;
TRUNCATE TABLE order_item_options;
TRUNCATE TABLE order_items;
TRUNCATE TABLE orders;
TRUNCATE TABLE product_option_groups;
TRUNCATE TABLE `options`;
TRUNCATE TABLE option_groups;
TRUNCATE TABLE store_product_stocks;
TRUNCATE TABLE product_images;
TRUNCATE TABLE products;
TRUNCATE TABLE category_sort_schedule_items;
TRUNCATE TABLE category_sort_schedules;
TRUNCATE TABLE product_categories;
TRUNCATE TABLE print_jobs;
TRUNCATE TABLE customer_preferences;
TRUNCATE TABLE customer_alipay_accounts;
TRUNCATE TABLE customer_wechat_accounts;
TRUNCATE TABLE customer_coupons;
TRUNCATE TABLE customers;
TRUNCATE TABLE coupon_templates;
TRUNCATE TABLE member_levels;
TRUNCATE TABLE store_settings;
TRUNCATE TABLE `tables`;
TRUNCATE TABLE stores;
TRUNCATE TABLE operation_logs;
TRUNCATE TABLE users;
TRUNCATE TABLE role_permissions;
TRUNCATE TABLE permissions;
TRUNCATE TABLE roles;
TRUNCATE TABLE system_settings;

SET FOREIGN_KEY_CHECKS = 1;

-- 一、角色
INSERT INTO roles (code, name, description)
SELECT 'SUPER_ADMIN', '超级管理员', '跨店全部权限'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'SUPER_ADMIN');

INSERT INTO roles (code, name, description)
SELECT 'STORE_ADMIN', '门店管理员', '本店运营（含退款；不含全局系统配置）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'STORE_ADMIN');

INSERT INTO roles (code, name, description)
SELECT 'CASHIER', '收银员', '订单、支付与收银点餐（不可退款）'
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
SELECT '金卡会员', 500, 0.9500 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM member_levels WHERE name = '金卡会员');

-- 五、券模板（VOUCHER 一次性核销；应付须≥面额）
-- 注册赠券：名称须与 .env WELCOME_COUPON_NAME 一致（默认「新客满30减5」）
INSERT INTO coupon_templates (name, coupon_type, discount_value, min_order_amount, valid_days)
SELECT '新客满30减5', 'FIXED', 5.00, 30.00, 30 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM coupon_templates WHERE name = '新客满30减5');

INSERT INTO coupon_templates (name, coupon_type, discount_value, min_order_amount, valid_days)
SELECT '满50享打9折', 'PERCENT', 0.9000, 50.00, 30 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM coupon_templates WHERE name = '满50享打9折');

INSERT INTO coupon_templates (name, coupon_type, discount_value, min_order_amount, valid_days)
SELECT '10元代金券', 'VOUCHER', 10.00, 0.00, 30 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM coupon_templates WHERE name = '10元代金券');

-- 六、门店
INSERT INTO stores (store_no, name, address, phone, status)
SELECT 'SH01', '肠旺面', '城南红砖路88号', '151-9691-4600', 1
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM stores WHERE store_no = 'SH01');

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
SELECT 'kitchen_auto_complete_minutes', '30', '厨房 KDS 未处理自动出餐（分钟，0=关闭）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'kitchen_auto_complete_minutes');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'delivery_auto_confirm', 'false', '新外卖单是否自动向平台 confirm'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'delivery_auto_confirm');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'welcome_coupon_name', '新客满30减5', '注册赠券模板名'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'welcome_coupon_name');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_alipay_avatar', 'true', '支付宝头像能力'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'enable_alipay_avatar');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_alipay_nickname', 'true', '支付宝昵称能力'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'enable_alipay_nickname');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_alipay_phone_bind', 'false', '支付宝手机号绑定'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'enable_alipay_phone_bind');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_wx_avatar', 'true', '微信头像能力'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'enable_wx_avatar');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_wx_nickname', 'true', '微信昵称能力'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'enable_wx_nickname');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_wx_phone_bind', 'false', '微信手机号绑定'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'enable_wx_phone_bind');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'kitchen_ticket_order_no', 'barcode', '厨房票订单号样式 barcode/text/off'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'kitchen_ticket_order_no');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'xpyun_print_copies', '1', '小票打印份数'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'xpyun_print_copies');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'xpyun_voice', '2', '云打印语音 0~3'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'xpyun_voice');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'max_upload_size_mb', '5', '单文件上传上限 MB'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'max_upload_size_mb');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'jwt_expire_minutes', '1440', '员工 Token 有效期（分钟）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'jwt_expire_minutes');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'customer_jwt_expire_minutes', '1440', '顾客 Token 有效期（分钟）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'customer_jwt_expire_minutes');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'webhook_ip_allowlist', '', 'Webhook IP 白名单'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'webhook_ip_allowlist');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'webhook_require_allowlist', 'false', '强制 Webhook 白名单'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'webhook_require_allowlist');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'trusted_proxy_ips', '127.0.0.1,::1', '可信反代 IP'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'trusted_proxy_ips');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'h5_public_base_url', '', 'H5 对外域名'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'h5_public_base_url');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'dev_lan_host', '', '开发机局域网 IP'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'dev_lan_host');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'mini_program_env_version', 'auto', '小程序码版本'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'mini_program_env_version');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'xpyun_enabled', 'false', '厨房打印总开关'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'xpyun_enabled');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'printer_mode', 'local', '打印机模式'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'printer_mode');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'printer_host', '', '打印机 IP'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'printer_host');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'printer_port', '9100', '打印机端口'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'printer_port');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'wechat_subscribe_template_order_ready', '', '微信出餐订阅模板'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'wechat_subscribe_template_order_ready');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'allowed_image_extensions', 'jpg,jpeg,png,webp,gif', '允许的图片扩展名'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'allowed_image_extensions');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'app_timezone', 'Asia/Shanghai', '业务时区'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'app_timezone');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'delivery_env_fallback_store_id', '0', '外卖 .env 兜底门店'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'delivery_env_fallback_store_id');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'preference_scene_enabled', 'true', '口味场景加权（季节/同星期同餐段）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'preference_scene_enabled');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'weather_pref_enabled', 'true', '天气口味加权（需门店经纬度）'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'weather_pref_enabled');

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
INSERT INTO `smart_noodle_house`.`users` (`id`, `username`, `password_hash`, `display_name`, `role_id`, `store_id`, `token_version`, `status`, `last_login_at`, `created_at`, `updated_at`) VALUES 
(1, 'admin', '$2b$12$38dPQAo/7seG5MQ9uCaLg.eysxss7vHFFOYSORWyAO5sNflaFgaQm', '系统管理员', 1, NULL, 0, 1, NULL, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(2, 'hsf', '$2b$12$5YuLHbzaVpVkUufQVLzI7.kgcmx8llwd8orUyt9APM8C.r1VLGSj.', '贺师傅', 4, 1, 1, 1, NULL, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(3, 'syxm', '$2b$12$0wcpcyCEiMt022onlYQ7AuwB9NHNIpzWjB0.Cx5bkOyCRDbZXcSiO', '收银小妹', 3, 1, 0, 1, NULL, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(4, 'cwm', '$2b$12$WDsX8M9O372aLwPVDvml7eTRqjcx5CvYDRzpb8AvdeHiavfkMikH2', '肠旺面店长', 2, 1, 0, 1, NULL, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');


-- 添加分类
INSERT INTO `smart_noodle_house`.`product_categories` (`store_id`, `category_no`, `name`, `description`, `sort_order`, `status`, `created_at`, `updated_at`) VALUES 
(1, 'SH01-TeJiaPin', '特价品', '超级优惠', 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, 'SH01-MianYangMiFen', '绵阳米粉', '经典绵阳米粉', 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, 'SH01-Mian', '面', '面', 2, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, 'SH01-XiaoChi', '小吃', '小吃', 3, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, 'SH01-LuWei', '卤味', '卤味', 4, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, 'SH01-YinLiao', '饮料', '饮料', 5, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 添加规格租
-- store_id NULL = 总部共享（全店可绑）；与 001 option_groups.store_id 语义一致
INSERT INTO `smart_noodle_house`.`option_groups` (`store_id`, `name`, `code`, `selection_type`, `is_required`, `sort_order`, `status`, `created_at`, `updated_at`) VALUES
(NULL, '量', '0000-Liang', 'SINGLE', 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(NULL, '加料', '0000-JiaLiao', 'MULTIPLE', 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(NULL, '辣', '0000-La', 'SINGLE', 0, 2, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(NULL, '麻', '0000-Ma', 'SINGLE', 0, 3, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(NULL, '葱', '0000-Cong', 'SINGLE', 0, 4, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(NULL, '香菜', '0000-XiangCai', 'SINGLE', 0, 5, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(NULL, '蒜末', '0000-SuanMo', 'SINGLE', 0, 6, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 添加规格组明细
  -- 量
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES 
(1, '小碗', '0000-Liang-XiaoWan', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, '中碗', '0000-Liang-ZhongWan', 1.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, '大碗', '0000-Liang-DaWAN', 2.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

  -- 加料
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES 
(2, '加卤蛋', '0000-JiaLiao-LuDan', 2.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(2, '加牛肉', '0000-JiaLiao-NiuRou', 5.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(2, '加肥肠', '0000-JiaLiao-FeiChang', 5.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

  -- 辣
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES 
(3, '不辣', '0000-La-BuLa', 0.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(3, '微辣', '0000-La-WeiLa', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(3, '正常辣', '0000-La-ZhengChangLa', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(3, '加辣', '0000-La-JiaLa', 0.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

  -- 麻
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES 
(4, '不麻', '0000-Ma-BuMa', 0.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(4, '微麻', '0000-Ma-WeiMa', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(4, '正常麻', '0000-Ma-ZhengChangMa', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(4, '加麻', '0000-Ma-JiaMa', 0.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

  -- 葱
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES 
(5, '不加葱', '0000-Cong-BuJiaCong', 0.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'), 
(5, '微量葱', '0000-Cong-WeiLiangCong', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'), 
(5, '正常葱', '0000-Cong-ZhengChangCong', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(5, '加量葱', '0000-Cong-JiaLiangCong', 0.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

  -- 香菜
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES 
(6, '不加香菜', '0000-XiangCai-BuJiaXiangCai', 0.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'), 
(6, '微量香菜', '0000-XiangCai-WeiLiangXiangCai', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(6, '正常香菜', '0000-XiangCai-ZhengChangXiangCai', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(6, '加量香菜', '0000-XiangCai-JiaLiangXiangCai', 0.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

  -- 蒜
INSERT INTO `smart_noodle_house`.`options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`) VALUES 
(7, '不加蒜末', '0000-SuanMo-BuJiaSuanMo', 0.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(7, '微量蒜末', '0000-SuanMo-WeiLiangSuanMo', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(7, '正常蒜末', '0000-SuanMo-ZhengChangSuanMo', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(7, '加量蒜末', '0000-SuanMo-JiaLiangSuanMo', 0.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 添加餐品
INSERT INTO products (
  store_id, category_id, product_no, name, description, main_image,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count
)
SELECT s.id, c.id, 'SH01-Mian-NiuRouMian', '牛肉面', '好吃的牛肉面', '/uploads/products/25985ea8d2fe4d08b074f5b4e443d57d.png',
  7.00, 6.00, 9999, 1.00, 1,
  'ON_SALE', 1, 1, 0, 0, 0, 1
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '面'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-Mian-NiuRouMian');

INSERT INTO products (
  store_id, category_id, product_no, name, description, main_image,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count
)
SELECT s.id, c.id, 'SH01-Mian-FeiChangMian', '肥肠面', '好吃的肥肠面', '/uploads/products/8681823f4fd646789b1915d1a82b4eab.png',
  7.00, 6.00, 9999, 1.00, 1,
  'ON_SALE', 1, 1, 0, 0, 0, 1
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '面'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-Mian-FeiChangMian');

INSERT INTO products (
  store_id, category_id, product_no, name, description, main_image,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count
)
SELECT s.id, c.id, 'SH01-MianYangMiFen-NiuRouMiFen', '牛肉米粉', '好吃的牛肉米粉', '/uploads/products/77ab8543ca8a445885909f048a946e46.png',
  7.00, 6.00, 9999, 1.00, 1,
  'ON_SALE', 1, 1, 0, 0, 0, 1
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '绵阳米粉'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-MianYangMiFen-NiuRouMiFen');

INSERT INTO products (
  store_id, category_id, product_no, name, description, main_image,
  price, original_price, stock, box_price, min_order_count,
  status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count
)
SELECT s.id, c.id, 'SH01-MianYangMiFen-FeiChangMiFen', '肥肠米粉', '好吃的肥肠米粉', '/uploads/products/12537c63a3b84e0da4eae8ef570b6ae4.png',
  7.00, 6.00, 9999, 1.00, 1,
  'ON_SALE', 1, 1, 0, 0, 0, 1
FROM stores s
JOIN product_categories c ON c.store_id = s.id AND c.name = '绵阳米粉'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-MianYangMiFen-FeiChangMiFen');

-- 关联商品规格
INSERT INTO `smart_noodle_house`.`product_option_groups` (`id`, `product_id`, `group_id`, `sort_order`) VALUES 
(1, 1, 1, 0), (2, 1, 2, 1), (3, 1, 3, 2), (4, 1, 4, 3), (5, 1, 5, 4), (6, 1, 6, 5), (7, 1, 7, 6),
(8, 2, 1, 0), (9, 2, 2, 1), (10, 2, 3, 2), (11, 2, 4, 3), (12, 2, 5, 4), (13, 2, 6, 5), (14, 2, 7, 6),
(15, 3, 1, 0), (16, 3, 2, 1), (17, 3, 3, 2), (18, 3, 4, 3), (19, 3, 5, 4), (20, 3, 6, 5), (21, 3, 7, 6),
(22, 4, 1, 0), (23, 4, 2, 1), (24, 4, 3, 2), (25, 4, 4, 3), (26, 4, 5, 4), (27, 4, 6, 5), (28, 4, 7, 6);


-- 添加桌号（WM=外带虚拟通道，非实体桌；堂食用 A01/A02）
INSERT INTO `smart_noodle_house`.`tables` (`id`, `store_id`, `table_no`, `table_name`, `qr_token`, `wechat_qr_url`, `alipay_qr_url`, `status`, `created_at`, `updated_at`) VALUES 
(1, 1, 'WM', '外带自取', 'M7v-8H_ZLOdUXsR9UYPH7Umj-ZU0Z4JuEijy5X_w3kg', 'weixin://dl/business/?appid=wxd0964c5d02b1549d&path=pages/index/index&query=token%3DM7v-8H_ZLOdUXsR9UYPH7Umj-ZU0Z4JuEijy5X_w3kg%26channel%3Dwechat&env_version=develop', 'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages%2Findex%2Findex&query=token%3DM7v-8H_ZLOdUXsR9UYPH7Umj-ZU0Z4JuEijy5X_w3kg%26channel%3Dalipay', 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(2, 1, 'A01', '1号桌', '6jO4K28UbqPozFWvtT0uqPpR5WWwD3030ir2ORwQYWs', 'weixin://dl/business/?appid=wxd0964c5d02b1549d&path=pages/index/index&query=token%3D6jO4K28UbqPozFWvtT0uqPpR5WWwD3030ir2ORwQYWs%26channel%3Dwechat&env_version=develop', 'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages%2Findex%2Findex&query=token%3D6jO4K28UbqPozFWvtT0uqPpR5WWwD3030ir2ORwQYWs%26channel%3Dalipay', 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(3, 1, 'A02', '2号桌', 'wJYZpvc1g-FqvZ7KE-XtFaS_M115B6KCcm93Cav6VbA', 'weixin://dl/business/?appid=wxd0964c5d02b1549d&path=pages/index/index&query=token%3DwJYZpvc1g-FqvZ7KE-XtFaS_M115B6KCcm93Cav6VbA%26channel%3Dwechat&env_version=develop', 'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages%2Findex%2Findex&query=token%3DwJYZpvc1g-FqvZ7KE-XtFaS_M115B6KCcm93Cav6VbA%26channel%3Dalipay', 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');


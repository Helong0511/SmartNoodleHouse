-- 切换数据库
USE `smart_noodle_house`;

-- 设置字符集
SET NAMES utf8mb4;

-- 禁用外键检查
SET FOREIGN_KEY_CHECKS = 0;

-- 清空所有表
TRUNCATE TABLE `ab_assignments`;
TRUNCATE TABLE `ab_experiments`;
TRUNCATE TABLE `cameras`;
TRUNCATE TABLE `cashier_shifts`;
TRUNCATE TABLE `category_sort_schedule_items`;
TRUNCATE TABLE `category_sort_schedules`;
TRUNCATE TABLE `coupon_templates`;
TRUNCATE TABLE `customer_alipay_accounts`;
TRUNCATE TABLE `customer_behavior_events`;
TRUNCATE TABLE `customer_coupons`;
TRUNCATE TABLE `customer_identities`;
TRUNCATE TABLE `customer_preferences`;
TRUNCATE TABLE `customer_taste_profiles`;
TRUNCATE TABLE `customer_wechat_accounts`;
TRUNCATE TABLE `customers`;
TRUNCATE TABLE `delivery_callback_logs`;
TRUNCATE TABLE `delivery_orders`;
TRUNCATE TABLE `delivery_product_maps`;
TRUNCATE TABLE `delivery_refunds`;
TRUNCATE TABLE `delivery_sync_alerts`;
TRUNCATE TABLE `dining_sessions`;
TRUNCATE TABLE `face_profiles`;
TRUNCATE TABLE `financial_ledger`;
TRUNCATE TABLE `member_levels`;
TRUNCATE TABLE `operation_logs`;
TRUNCATE TABLE `option_groups`;
TRUNCATE TABLE `options`;
TRUNCATE TABLE `order_item_options`;
TRUNCATE TABLE `order_items`;
TRUNCATE TABLE `order_status_history`;
TRUNCATE TABLE `orders`;
TRUNCATE TABLE `outbox_events`;
TRUNCATE TABLE `payment_callback_receipts`;
TRUNCATE TABLE `payment_refunds`;
TRUNCATE TABLE `payment_status_history`;
TRUNCATE TABLE `payments`;
TRUNCATE TABLE `permissions`;
TRUNCATE TABLE `platform_category_maps`;
TRUNCATE TABLE `platform_store_bindings`;
TRUNCATE TABLE `point_ledger`;
TRUNCATE TABLE `pos_refund_requests`;
TRUNCATE TABLE `print_jobs`;
TRUNCATE TABLE `product_categories`;
TRUNCATE TABLE `product_feedbacks`;
TRUNCATE TABLE `product_images`;
TRUNCATE TABLE `product_option_groups`;
TRUNCATE TABLE `product_taste_profiles`;
TRUNCATE TABLE `products`;
TRUNCATE TABLE `role_permissions`;
TRUNCATE TABLE `roles`;
TRUNCATE TABLE `store_business_hours`;
TRUNCATE TABLE `store_product_stocks`;
TRUNCATE TABLE `store_settings`;
TRUNCATE TABLE `stores`;
TRUNCATE TABLE `system_settings`;
TRUNCATE TABLE `tables`;
TRUNCATE TABLE `users`;
TRUNCATE TABLE `visitor_events`;
TRUNCATE TABLE `webhook_inbox`;
TRUNCATE TABLE `welcome_logs`;

-- 一、角色
INSERT INTO `roles` (`code`, `name`, `description`)
SELECT 
  'SUPER_ADMIN',
  '超级管理员',
  '跨店全部权限'
FROM `DUAL`
WHERE NOT EXISTS (
    SELECT 1
    FROM `roles`
    WHERE `code` = 'SUPER_ADMIN'
  );
INSERT INTO `roles` (`code`, `name`, `description`)
SELECT 
  'STORE_ADMIN',
  '门店管理员',
  '本店运营（含退款；不含全局系统配置）'
FROM `DUAL`
WHERE NOT EXISTS (
    SELECT 1
    FROM roles
    WHERE code = 'STORE_ADMIN'
  );
INSERT INTO roles (code, name, description)
SELECT 
  'CASHIER',
  '收银员',
  '订单、支付与收银点餐（不可退款）'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM roles
    WHERE code = 'CASHIER'
  );
INSERT INTO roles (code, name, description)
SELECT 
  'KITCHEN',
  '厨房',
  'KDS 出餐（不可退款/不可审外卖退款）'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM roles
    WHERE code = 'KITCHEN'
);
INSERT INTO roles (code, name, description)
SELECT 'OPERATOR',
  '运营',
  '商品与数据（不可退款）'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM roles
    WHERE code = 'OPERATOR'
  );
-- 二、权限点
INSERT INTO permissions (code, name, module)
SELECT 'product:read',
  '查看商品',
  'product'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'product:read'
  );
INSERT INTO permissions (code, name, module)
SELECT 'product:create',
  '创建商品',
  'product'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'product:create'
  );
INSERT INTO permissions (code, name, module)
SELECT 'product:update',
  '更新商品',
  'product'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'product:update'
  );
INSERT INTO permissions (code, name, module)
SELECT 'product:delete',
  '删除商品',
  'product'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'product:delete'
  );
INSERT INTO permissions (code, name, module)
SELECT 'order:read',
  '查看订单',
  'order'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'order:read'
  );
INSERT INTO permissions (code, name, module)
SELECT 'order:update',
  '更新订单',
  'order'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'order:update'
  );
INSERT INTO permissions (code, name, module)
SELECT 'customer:read',
  '查看顾客',
  'customer'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'customer:read'
  );
INSERT INTO permissions (code, name, module)
SELECT 'customer:manage',
  '管理会员等级',
  'customer'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'customer:manage'
  );
INSERT INTO permissions (code, name, module)
SELECT 'camera:read',
  '查看摄像头',
  'camera'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'camera:read'
  );
INSERT INTO permissions (code, name, module)
SELECT 'camera:manage',
  '管理摄像头',
  'camera'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'camera:manage'
  );
INSERT INTO permissions (code, name, module)
SELECT 'welcome:manage',
  '管理迎宾',
  'welcome'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'welcome:manage'
  );
INSERT INTO permissions (code, name, module)
SELECT 'payment:read',
  '查看支付',
  'payment'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'payment:read'
  );
INSERT INTO permissions (code, name, module)
SELECT 'payment:refund',
  '支付与外卖退款',
  'payment'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'payment:refund'
  );
INSERT INTO permissions (code, name, module)
SELECT 'table:manage',
  '管理桌台',
  'table'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'table:manage'
  );
INSERT INTO permissions (code, name, module)
SELECT 'user:read',
  '查看员工',
  'user'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'user:read'
  );
INSERT INTO permissions (code, name, module)
SELECT 'user:manage',
  '管理员工',
  'user'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'user:manage'
  );
INSERT INTO permissions (code, name, module)
SELECT 'system:manage',
  '系统管理',
  'system'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'system:manage'
  );
INSERT INTO permissions (code, name, module)
SELECT 'coupon:read',
  '查看优惠券',
  'coupon'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'coupon:read'
  );
INSERT INTO permissions (code, name, module)
SELECT 'coupon:manage',
  '管理优惠券',
  'coupon'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'coupon:manage'
  );
INSERT INTO permissions (code, name, module)
SELECT 'audit:read',
  '查看操作日志',
  'audit'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM permissions
    WHERE code = 'audit:read'
  );
-- 三、角色权限矩阵
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id,
  p.id
FROM roles r
  CROSS JOIN permissions p
WHERE r.code = 'SUPER_ADMIN'
  AND NOT EXISTS (
    SELECT 1
    FROM role_permissions rp
    WHERE rp.role_id = r.id
      AND rp.permission_id = p.id
  );
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id,
  p.id
FROM roles r
  JOIN permissions p ON p.code IN (
    'product:read',
    'product:create',
    'product:update',
    'product:delete',
    'order:read',
    'order:update',
    'customer:read',
    'customer:manage',
    'camera:read',
    'camera:manage',
    'welcome:manage',
    'payment:read',
    'payment:refund',
    'table:manage',
    'user:read',
    'user:manage',
    'coupon:read',
    'coupon:manage',
    'audit:read'
  )
WHERE r.code = 'STORE_ADMIN'
  AND NOT EXISTS (
    SELECT 1
    FROM role_permissions rp
    WHERE rp.role_id = r.id
      AND rp.permission_id = p.id
  );
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id,
  p.id
FROM roles r
  JOIN permissions p ON p.code IN ('order:read', 'order:update')
WHERE r.code = 'KITCHEN'
  AND NOT EXISTS (
    SELECT 1
    FROM role_permissions rp
    WHERE rp.role_id = r.id
      AND rp.permission_id = p.id
  );
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id,
  p.id
FROM roles r
  JOIN permissions p ON p.code IN (
    'order:read',
    'order:update',
    'payment:read',
    'customer:read',
    'product:read'
  )
WHERE r.code = 'CASHIER'
  AND NOT EXISTS (
    SELECT 1
    FROM role_permissions rp
    WHERE rp.role_id = r.id
      AND rp.permission_id = p.id
  );
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id,
  p.id
FROM roles r
  JOIN permissions p ON p.code IN (
    'product:read',
    'product:create',
    'product:update',
    'product:delete',
    'order:read',
    'customer:read',
    'payment:read'
  )
WHERE r.code = 'OPERATOR'
  AND NOT EXISTS (
    SELECT 1
    FROM role_permissions rp
    WHERE rp.role_id = r.id
      AND rp.permission_id = p.id
  );
-- 四、会员等级
INSERT INTO member_levels (name, min_points, discount_rate)
SELECT '普通会员',
  0,
  1.0000
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM member_levels
    WHERE name = '普通会员'
  );
INSERT INTO member_levels (name, min_points, discount_rate)
SELECT '银卡会员',
  200,
  0.9800
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM member_levels
    WHERE name = '银卡会员'
  );
INSERT INTO member_levels (name, min_points, discount_rate)
SELECT '金卡会员',
  500,
  0.9500
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM member_levels
    WHERE name = '金卡会员'
  );
-- 五、券模板（VOUCHER 一次性核销；应付须≥面额）
-- 注册赠券：名称须与 .env WELCOME_COUPON_NAME 一致（默认「新客满30减5」）
INSERT INTO coupon_templates (
    name,
    coupon_type,
    discount_value,
    min_order_amount,
    valid_days
  )
SELECT '新客满30减5',
  'FIXED',
  5.00,
  30.00,
  30
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM coupon_templates
    WHERE name = '新客满30减5'
  );
INSERT INTO coupon_templates (
    name,
    coupon_type,
    discount_value,
    min_order_amount,
    valid_days
  )
SELECT '满50享打9折',
  'PERCENT',
  0.9000,
  50.00,
  30
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM coupon_templates
    WHERE name = '满50享打9折'
  );
INSERT INTO coupon_templates (
    name,
    coupon_type,
    discount_value,
    min_order_amount,
    valid_days
  )
SELECT '10元代金券',
  'VOUCHER',
  10.00,
  0.00,
  30
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM coupon_templates
    WHERE name = '10元代金券'
  );
-- 六、门店
INSERT INTO `smart_noodle_house`.`stores` (
    `id`,
    `store_no`,
    `name`,
    `address`,
    `phone`,
    `latitude`,
    `longitude`,
    `status`,
    `created_at`,
    `updated_at`
  )
VALUES (
    1,
    'SH01',
    '肠旺面(总店)',
    '城南红砖路88号',
    '151-9691-4600',
    30.8719,
    105.3884,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    2,
    'SH02',
    '肠旺面(二店)',
    '城南红砖路188号',
    '151-9691-4600',
    30.8719,
    105.3884,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- 七、系统参数
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'order_payment_timeout_minutes',
  '15',
  '订单支付超时分钟数'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'order_payment_timeout_minutes'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'visitor_data_retention_days',
  '7',
  '匿名访客数据保留天数'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'visitor_data_retention_days'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'welcome_cooldown_seconds',
  '60',
  '迎宾冷却秒数'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'welcome_cooldown_seconds'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'face_match_threshold',
  '0.75',
  '人脸匹配阈值'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'face_match_threshold'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'face_confirm_frames',
  '5',
  '人脸确认帧数'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'face_confirm_frames'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'operation_log_retention_days',
  '60',
  '操作日志保留天数'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'operation_log_retention_days'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'kitchen_auto_complete_minutes',
  '30',
  '厨房 KDS 未处理自动出餐（分钟，0=关闭）'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'kitchen_auto_complete_minutes'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'delivery_auto_confirm',
  'false',
  '新外卖单是否自动向平台 confirm'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'delivery_auto_confirm'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'welcome_coupon_name',
  '新客满30减5',
  '注册赠券模板名'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'welcome_coupon_name'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_alipay_avatar',
  'true',
  '支付宝头像能力'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'enable_alipay_avatar'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_alipay_nickname',
  'true',
  '支付宝昵称能力'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'enable_alipay_nickname'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_alipay_phone_bind',
  'false',
  '支付宝手机号绑定'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'enable_alipay_phone_bind'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_wx_avatar',
  'true',
  '微信头像能力'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'enable_wx_avatar'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_wx_nickname',
  'true',
  '微信昵称能力'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'enable_wx_nickname'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'enable_wx_phone_bind',
  'false',
  '微信手机号绑定'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'enable_wx_phone_bind'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'max_upload_size_mb',
  '5',
  '单文件上传上限 MB'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'max_upload_size_mb'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'jwt_expire_minutes',
  '1440',
  '员工 Token 有效期（分钟）'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'jwt_expire_minutes'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'customer_jwt_expire_minutes',
  '1440',
  '顾客 Token 有效期（分钟）'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'customer_jwt_expire_minutes'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'webhook_ip_allowlist',
  '',
  'Webhook IP 白名单'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'webhook_ip_allowlist'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'webhook_require_allowlist',
  'false',
  '强制 Webhook 白名单'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'webhook_require_allowlist'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'trusted_proxy_ips',
  '127.0.0.1,::1',
  '可信反代 IP'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'trusted_proxy_ips'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'h5_public_base_url',
  '',
  'H5 对外域名'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'h5_public_base_url'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'dev_lan_host',
  '',
  '开发机局域网 IP'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'dev_lan_host'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'mini_program_env_version',
  'auto',
  '小程序码版本'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'mini_program_env_version'
  );
-- true/false
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'xpyun_enabled',
  'false',
  '厨房打印总开关'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'xpyun_enabled'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'printer_mode',
  'local',
  '打印机模式'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'printer_mode'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'kitchen_ticket_order_no',
  'barcode',
  '厨房票订单号样式 barcode/text/off'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'kitchen_ticket_order_no'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'xpyun_print_copies',
  '1',
  '小票打印份数'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'xpyun_print_copies'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'xpyun_voice',
  '0',
  '云打印语音 0~3'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'xpyun_voice'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'printer_host',
  '192.168.1.150',
  '打印机 IP'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'printer_host'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'printer_port',
  '9100',
  '打印机端口'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'printer_port'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'wechat_subscribe_template_order_ready',
  '',
  '微信出餐订阅模板'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'wechat_subscribe_template_order_ready'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'allowed_image_extensions',
  'jpg,jpeg,png,webp,gif',
  '允许的图片扩展名'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'allowed_image_extensions'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'app_timezone',
  'Asia/Shanghai',
  '业务时区'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'app_timezone'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'delivery_env_fallback_store_id',
  '0',
  '外卖 .env 兜底门店'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'delivery_env_fallback_store_id'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'preference_scene_enabled',
  'true',
  '口味场景加权（季节/同星期同餐段）'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'preference_scene_enabled'
  );
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'weather_pref_enabled',
  'true',
  '天气口味加权（需门店经纬度）'
FROM DUAL
WHERE NOT EXISTS (
    SELECT 1
    FROM system_settings
    WHERE setting_key = 'weather_pref_enabled'
  );
-- 八、校正（幂等）：非店长角色不得持有 payment:refund
DELETE rp
FROM role_permissions rp
  JOIN roles r ON r.id = rp.role_id
  JOIN permissions p ON p.id = rp.permission_id
WHERE r.code IN ('CASHIER', 'KITCHEN', 'OPERATOR')
  AND p.code = 'payment:refund';
UPDATE permissions
SET name = '支付与外卖退款'
WHERE code = 'payment:refund';
SELECT (
    SELECT COUNT(*)
    FROM roles
  ) AS roles,
  (
    SELECT COUNT(*)
    FROM permissions
  ) AS permissions,
  (
    SELECT COUNT(*)
    FROM stores
    WHERE status = 1
  ) AS active_stores;
-- 添加管理员账号
INSERT INTO `smart_noodle_house`.`users` (
    `id`,
    `username`,
    `password_hash`,
    `display_name`,
    `role_id`,
    `store_id`,
    `token_version`,
    `status`,
    `last_login_at`,
    `created_at`,
    `updated_at`
  )
VALUES (
    1,
    'admin',
    '$2b$12$38dPQAo/7seG5MQ9uCaLg.eysxss7vHFFOYSORWyAO5sNflaFgaQm',
    '系统管理员',
    1,
    NULL,
    0,
    1,
    NULL,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    2,
    'hsf',
    '$2b$12$5YuLHbzaVpVkUufQVLzI7.kgcmx8llwd8orUyt9APM8C.r1VLGSj.',
    '贺师傅',
    4,
    1,
    1,
    1,
    NULL,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    3,
    'syxm',
    '$2b$12$0wcpcyCEiMt022onlYQ7AuwB9NHNIpzWjB0.Cx5bkOyCRDbZXcSiO',
    '收银小妹',
    3,
    1,
    0,
    1,
    NULL,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    4,
    'cwm',
    '$2b$12$WDsX8M9O372aLwPVDvml7eTRqjcx5CvYDRzpb8AvdeHiavfkMikH2',
    '肠旺面店长',
    2,
    1,
    0,
    1,
    NULL,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO product_categories (
    store_id,
    category_no,
    name,
    description,
    sort_order,
    status,
    created_at,
    updated_at
  )
VALUES (
    1,
    'SH01-ZhaoPaiMian',
    '招牌面·汤面',
    '射洪招牌汤面：鸡杂/杂酱/豌杂/笋子牛肉/蹄花/肠旺面',
    6,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    1,
    'SH01-GanBanMian',
    '干拌面',
    '干拌宽面、烧椒面（可切汤/干拌）',
    7,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    1,
    'SH01-ChaoShouShuiJiao',
    '抄手·水饺',
    '清汤/红油抄手与红油水饺',
    8,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    1,
    'SH01-TangYuan',
    '甜香汤圆',
    '汤圆荷包蛋、红糖小汤圆（甜品，咸甜分开）',
    9,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    1,
    'SH01-BaoZiDianXin',
    '包子·点心',
    '香葱包、酱肉包，支持单个起售',
    10,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- ============================================================================
-- 二、新增规格组（1-13：量/加料/辣/麻/葱/香菜/蒜末/姜末/面型/口感/汤汁/加臊子/
--     加蛋加菜/汤底/皮薄厚/甜度/荷包蛋熟度/汤圆数量/包子个数/是否加热）
--     store_id=NULL 表示总部共享，全店可绑（与原有 0000- 编码一致）
-- ============================================================================
INSERT INTO option_groups (
    id,
    store_id,
    name,
    code,
    selection_type,
    is_required,
    sort_order,
    status,
    created_at,
    updated_at
  )
VALUES (
    1,
    1,
    '姜末',
    'SH01-JiangMo',
    'SINGLE',
    0,
    7,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    2,
    1,
    '面型',
    'SH01-MianXing',
    'SINGLE',
    0,
    8,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    3,
    1,
    '口感',
    'SH01-KouGan',
    'SINGLE',
    0,
    9,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    4,
    1,
    '汤汁',
    'SH01-TangZhi',
    'SINGLE',
    0,
    10,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    5,
    1,
    '加臊子',
    'SH01-JiaSaoZi',
    'MULTIPLE',
    0,
    11,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    6,
    1,
    '加蛋加菜',
    'SH01-JiaDanJiaCai',
    'MULTIPLE',
    0,
    12,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    7,
    1,
    '汤底',
    'SH01-TangDi',
    'SINGLE',
    0,
    13,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    8,
    1,
    '皮薄厚',
    'SH01-PiBaoHou',
    'SINGLE',
    0,
    14,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    9,
    1,
    '甜度',
    'SH01-TianDu',
    'SINGLE',
    0,
    15,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    10,
    1,
    '荷包蛋熟度',
    'SH01-ShuDu',
    'SINGLE',
    0,
    16,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    11,
    1,
    '汤圆数量',
    'SH01-TangYuanShuLiang',
    'SINGLE',
    1,
    17,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    12,
    1,
    '包子个数',
    'SH01-BaoZiGeShu',
    'SINGLE',
    1,
    18,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    13,
    1,
    '是否加热',
    'SH01-ShiFouJiaRe',
    'SINGLE',
    1,
    19,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- ============================================================================
-- 三、新增规格选项
-- ============================================================================
-- 3.1 姜末（group 1）★ 用户要求的葱/姜/蒜之“姜”
INSERT INTO options (
    group_id,
    name,
    code,
    extra_price,
    sort_order,
    is_default,
    status,
    created_at,
    updated_at
  )
VALUES (
    1,
    '不加姜末',
    'SH01-JiangMo-BuJiaJiangMo',
    0.00,
    0,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    1,
    '微量姜末',
    'SH01-JiangMo-WeiLiangJiangMo',
    0.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    1,
    '正常姜末',
    'SH01-JiangMo-ZhengChangJiangMo',
    0.00,
    2,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    1,
    '加量姜末',
    'SH01-JiangMo-JiaLiangJiangMo',
    0.00,
    3,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- 3.2 面型（group 2）
INSERT INTO options (
    group_id,
    name,
    code,
    extra_price,
    sort_order,
    is_default,
    status,
    created_at,
    updated_at
  )
VALUES (
    2,
    '细面',
    'SH01-MianXing-XiMian',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    2,
    '宽面',
    'SH01-MianXing-KuanMian',
    0.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    2,
    '韭菜叶',
    'SH01-MianXing-JiuCaiYe',
    0.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    2,
    '碱水面',
    'SH01-MianXing-JianShuiMian',
    0.00,
    3,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- 3.3 口感（group 3）
INSERT INTO options (
    group_id,
    name,
    code,
    extra_price,
    sort_order,
    is_default,
    status,
    created_at,
    updated_at
  )
VALUES (
    3,
    '软一点',
    'SH01-KouGan-RuanYiDian',
    0.00,
    0,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    3,
    '标准',
    'SH01-KouGan-BiaoZhun',
    0.00,
    1,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    3,
    '硬一点(提黄)',
    'SH01-KouGan-YingYiDian',
    0.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- 3.4 汤汁（group 4）
INSERT INTO options (
    group_id,
    name,
    code,
    extra_price,
    sort_order,
    is_default,
    status,
    created_at,
    updated_at
  )
VALUES (
    4,
    '正常汤',
    'SH01-TangZhi-ZhengChangTang',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    4,
    '多汤',
    'SH01-TangZhi-DuoTang',
    0.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    4,
    '少汤',
    'SH01-TangZhi-ShaoTang',
    0.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    4,
    '干拌(免汤)',
    'SH01-TangZhi-GanBan',
    0.00,
    3,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- 3.5 加臊子（group 5，多选加价；牛肉/肥肠沿用原 group 2 加料）
INSERT INTO options (
    group_id,
    name,
    code,
    extra_price,
    sort_order,
    is_default,
    status,
    created_at,
    updated_at
  )
VALUES (
    5,
    '加杂酱',
    'SH01-JiaSaoZi-ZaJiang',
    3.00,
    0,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    5,
    '加鸡杂',
    'SH01-JiaSaoZi-JiZa',
    4.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    5,
    '加蹄花',
    'SH01-JiaSaoZi-TiHua',
    6.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    5,
    '加血旺',
    'SH01-JiaSaoZi-XueWang',
    3.00,
    3,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- 3.6 加蛋加菜（group 6）
INSERT INTO options (
    group_id,
    name,
    code,
    extra_price,
    sort_order,
    is_default,
    status,
    created_at,
    updated_at
  )
VALUES (
    6,
    '加煎蛋',
    'SH01-JiaDanJiaCai-JianDan',
    2.00,
    0,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    6,
    '加荷包蛋',
    'SH01-JiaDanJiaCai-HeBaoDan',
    2.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    6,
    '加豌豆',
    'SH01-JiaDanJiaCai-WanDou',
    1.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    6,
    '加青菜',
    'SH01-JiaDanJiaCai-QingCai',
    1.00,
    3,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    6,
    '加豆芽',
    'SH01-JiaDanJiaCai-DouYa',
    1.00,
    4,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- 3.7 汤底（group 7，抄手/水饺）
INSERT INTO options (
    group_id,
    name,
    code,
    extra_price,
    sort_order,
    is_default,
    status,
    created_at,
    updated_at
  )
VALUES (
    7,
    '清汤',
    'SH01-TangDi-QingTang',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    7,
    '红油',
    'SH01-TangDi-HongYou',
    0.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    7,
    '酸辣',
    'SH01-TangDi-SuanLa',
    0.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    7,
    '干拌',
    'SH01-TangDi-GanBan',
    0.00,
    3,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- 3.8 皮薄厚（group 8）
INSERT INTO options (
    group_id,
    name,
    code,
    extra_price,
    sort_order,
    is_default,
    status,
    created_at,
    updated_at
  )
VALUES (
    8,
    '标准',
    'SH01-PiBaoHou-BiaoZhun',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    8,
    '皮薄',
    'SH01-PiBaoHou-PiBao',
    0.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- 3.9 甜度（group 9，汤圆）
INSERT INTO options (
    group_id,
    name,
    code,
    extra_price,
    sort_order,
    is_default,
    status,
    created_at,
    updated_at
  )
VALUES (
    9,
    '正常糖',
    'SH01-TianDu-ZhengChangTang',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    9,
    '少糖',
    'SH01-TianDu-ShaoTang',
    0.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    9,
    '无糖',
    'SH01-TianDu-WuTang',
    0.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- 3.10 荷包蛋熟度（group 10）
INSERT INTO options (
    group_id,
    name,
    code,
    extra_price,
    sort_order,
    is_default,
    status,
    created_at,
    updated_at
  )
VALUES (
    10,
    '溏心',
    'SH01-ShuDu-TangXin',
    0.00,
    0,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    10,
    '全熟',
    'SH01-ShuDu-QuanShu',
    0.00,
    1,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- 3.11 汤圆数量（group 11，必选，默认 6 个）
INSERT INTO options (
    group_id,
    name,
    code,
    extra_price,
    sort_order,
    is_default,
    status,
    created_at,
    updated_at
  )
VALUES (
    11,
    '4个',
    'SH01-TangYuanShuLiang-4Ge',
    0.00,
    0,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    11,
    '6个',
    'SH01-TangYuanShuLiang-6Ge',
    0.00,
    1,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    11,
    '8个',
    'SH01-TangYuanShuLiang-8Ge',
    0.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- 3.12 包子个数（group 12；基础价=1个价，其余为相对加价）
INSERT INTO options (
    group_id,
    name,
    code,
    extra_price,
    sort_order,
    is_default,
    status,
    created_at,
    updated_at
  )
VALUES (
    12,
    '1个',
    'SH01-BaoZiGeShu-1Ge',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    12,
    '2个',
    'SH01-BaoZiGeShu-2Ge',
    2.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    12,
    '半笼(3个)',
    'SH01-BaoZiGeShu-BanLong',
    4.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    12,
    '一笼(6个)',
    'SH01-BaoZiGeShu-YiLong',
    10.00,
    3,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- 3.13 是否加热（group 13）
INSERT INTO options (
    group_id,
    name,
    code,
    extra_price,
    sort_order,
    is_default,
    status,
    created_at,
    updated_at
  )
VALUES (
    13,
    '现蒸',
    'SH01-ShiFouJiaRe-XianZheng',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    13,
    '复蒸加热',
    'SH01-ShiFouJiaRe-FuZheng',
    0.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- ============================================================================
-- 四、新增 14 道餐品
--     product_no 规则：{store_no}-分类逻辑-商品拼音
--     main_image：1200×1200 方图，实际文件由配图步骤生成到 /uploads/products/
-- ============================================================================
-- 4.1 招牌面·汤面 -----------------------------------------------------------
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-ZhaoPaiMian-JiZaMian',
  '鸡杂面',
  '爆炒鸡杂（鸡胗/鸡心）浇头，红汤细面，麻辣鲜香',
  '/uploads/products/jizamian.png',
  9.00,
  NULL,
  9999,
  1.00,
  1,
  'ON_SALE',
  0,
  0,
  0,
  0,
  0,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-ZhaoPaiMian'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-ZhaoPaiMian-JiZaMian'
  );
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-ZhaoPaiMian-ZaJiangMian',
  '杂酱面',
  '肉末杂酱臊子，酱色油亮，经典川味干香',
  '/uploads/products/zajiangmian.png',
  8.00,
  NULL,
  9999,
  1.00,
  1,
  'ON_SALE',
  0,
  1,
  0,
  0,
  1,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-ZhaoPaiMian'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-ZhaoPaiMian-ZaJiangMian'
  );
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-ZhaoPaiMian-WanZaMian',
  '豌杂面',
  '豌豆+杂酱双浇头，软糯耙豌豆配肉末（即“碗杂面”）',
  '/uploads/products/wanzamian.png',
  9.00,
  NULL,
  9999,
  1.00,
  1,
  'ON_SALE',
  0,
  0,
  0,
  0,
  2,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-ZhaoPaiMian'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-ZhaoPaiMian-WanZaMian'
  );
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-ZhaoPaiMian-SunZiNiuRouMian',
  '笋子牛肉面',
  '红烧牛肉块配鲜笋，红汤醇厚，大块牛肉满足',
  '/uploads/products/sunziniuroumian.png',
  12.00,
  NULL,
  9999,
  1.00,
  1,
  'ON_SALE',
  0,
  1,
  0,
  0,
  3,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-ZhaoPaiMian'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-ZhaoPaiMian-SunZiNiuRouMian'
  );
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-ZhaoPaiMian-TiHuaMian',
  '蹄花面',
  '炖耙糯猪蹄花，雪豆汤底，胶质浓稠',
  '/uploads/products/tihuamian.png',
  14.00,
  NULL,
  9999,
  1.00,
  1,
  'ON_SALE',
  0,
  0,
  0,
  0,
  4,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-ZhaoPaiMian'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-ZhaoPaiMian-TiHuaMian'
  );
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-ZhaoPaiMian-ChangWangMian',
  '肠旺面',
  '★招牌：卤肥肠+血旺（鸭血）双浇，红油汤底，射洪必点',
  '/uploads/products/changwangmian.png',
  12.00,
  NULL,
  9999,
  1.00,
  1,
  'ON_SALE',
  1,
  1,
  0,
  0,
  5,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-ZhaoPaiMian'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-ZhaoPaiMian-ChangWangMian'
  );
-- 4.2 干拌面 ---------------------------------------------------------------
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-GanBanMian-GanBanKuanMian',
  '干拌宽面',
  '宽面干拌，配杂酱/芽菜/花生碎，无汤更香',
  '/uploads/products/ganbankuanmian.png',
  8.00,
  NULL,
  9999,
  1.00,
  1,
  'ON_SALE',
  0,
  0,
  0,
  0,
  0,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-GanBanMian'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-GanBanMian-GanBanKuanMian'
  );
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-GanBanMian-ShaoJiaoMian',
  '烧椒面',
  '炭烧青椒剁碎拌面，糊辣清香（可选汤/干拌）',
  '/uploads/products/shaojiaomian.png',
  9.00,
  NULL,
  9999,
  1.00,
  1,
  'ON_SALE',
  0,
  0,
  0,
  0,
  1,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-GanBanMian'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-GanBanMian-ShaoJiaoMian'
  );
-- 4.3 抄手·水饺 -------------------------------------------------------------
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-ChaoShouShuiJiao-ChaoShou',
  '抄手',
  '薄皮猪肉抄手，可选清汤/红油/酸辣/干拌，皮薄馅嫩',
  '/uploads/products/chaoshou.png',
  8.00,
  NULL,
  9999,
  1.00,
  1,
  'ON_SALE',
  0,
  1,
  0,
  0,
  0,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-ChaoShouShuiJiao'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-ChaoShouShuiJiao-ChaoShou'
  );
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-ChaoShouShuiJiao-HongYouShuiJiao',
  '红油水饺',
  '皮薄馅大水饺，淋红亮辣椒油与复制酱油，撒芝麻葱花',
  '/uploads/products/hongyoushuijiao.png',
  9.00,
  NULL,
  9999,
  1.00,
  1,
  'ON_SALE',
  0,
  0,
  0,
  0,
  1,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-ChaoShouShuiJiao'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-ChaoShouShuiJiao-HongYouShuiJiao'
  );
-- 4.4 甜香汤圆 -------------------------------------------------------------
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-TangYuan-TangYuanHeBaoDan',
  '汤圆荷包蛋',
  '红糖水小汤圆配煎荷包蛋，甜香暖胃（早餐/夜宵）',
  '/uploads/products/tangyuanhebaodan.png',
  7.00,
  NULL,
  9999,
  1.00,
  1,
  'ON_SALE',
  0,
  0,
  0,
  0,
  0,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-TangYuan'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-TangYuan-TangYuanHeBaoDan'
  );
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-TangYuan-HongTangXiaoTangYuan',
  '红糖小汤圆',
  '糯米小汤圆卧浓稠红糖汁，撒熟白芝麻，甜而不腻',
  '/uploads/products/hongtangxiaotangyuan.png',
  6.00,
  NULL,
  9999,
  1.00,
  1,
  'ON_SALE',
  0,
  0,
  0,
  0,
  1,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-TangYuan'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-TangYuan-HongTangXiaoTangYuan'
  );
-- 4.5 包子·点心 -------------------------------------------------------------
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-BaoZiDianXin-XiangCongBao',
  '香葱包',
  '白白胖胖，面皮里裹新鲜香葱碎，咸香松软（1个起售）',
  '/uploads/products/xiangcongbao.png',
  2.00,
  NULL,
  9999,
  0.00,
  1,
  'ON_SALE',
  0,
  0,
  0,
  0,
  0,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-BaoZiDianXin'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-BaoZiDianXin-XiangCongBao'
  );
INSERT INTO products (
    store_id,
    category_id,
    product_no,
    name,
    description,
    main_image,
    price,
    original_price,
    stock,
    box_price,
    min_order_count,
    status,
    is_recommended,
    is_hot,
    no_member_discount,
    no_cooking,
    sort_order,
    sales_count
  )
SELECT s.id,
  c.id,
  'SH01-BaoZiDianXin-JiangRouBao',
  '酱肉包',
  '酱色猪肉馅，咸甜适口，汁香浓郁（1个起售）',
  '/uploads/products/jiangroubao.png',
  2.50,
  NULL,
  9999,
  0.00,
  1,
  'ON_SALE',
  0,
  0,
  0,
  0,
  1,
  0
FROM stores s
  JOIN product_categories c ON c.store_id = s.id
  AND c.category_no = 'SH01-BaoZiDianXin'
WHERE s.store_no = 'SH01'
  AND NOT EXISTS (
    SELECT 1
    FROM products p
    WHERE p.store_id = s.id
      AND p.product_no = 'SH01-BaoZiDianXin-JiangRouBao'
  );
-- ============================================================================
-- 五、商品-规格组绑定（按分类批量绑定，已存在则跳过）
-- ============================================================================
-- 5.1 招牌面·汤面 + 干拌面：完整面类规格（量/加料/辣/麻/葱/香菜/蒜末/姜末/面型/口感/汤汁/加臊子/加蛋加菜）
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id,
  g.id,
  g.sort_order
FROM products p
  JOIN product_categories c ON c.id = p.category_id
  JOIN option_groups g ON g.code IN (
    'SH01-Liang',
    'SH01-JiaLiao',
    'SH01-La',
    'SH01-Ma',
    'SH01-Cong',
    'SH01-XiangCai',
    'SH01-SuanMo',
    'SH01-JiangMo',
    'SH01-MianXing',
    'SH01-KouGan',
    'SH01-TangZhi',
    'SH01-JiaSaoZi',
    'SH01-JiaDanJiaCai'
  )
WHERE p.store_id = (
    SELECT id
    FROM stores
    WHERE store_no = 'SH01'
  )
  AND c.category_no IN ('SH01-ZhaoPaiMian', 'SH01-GanBanMian')
  AND NOT EXISTS (
    SELECT 1
    FROM product_option_groups pog
    WHERE pog.product_id = p.id
      AND pog.group_id = g.id
  );
-- 5.2 抄手·水饺：量(份)/加料/辣/麻/葱/香菜/蒜末/姜末/加蛋加菜/汤底/皮薄厚
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id,
  g.id,
  g.sort_order
FROM products p
  JOIN product_categories c ON c.id = p.category_id
  JOIN option_groups g ON g.code IN (
    'SH01-Liang',
    'SH01-JiaLiao',
    'SH01-La',
    'SH01-Ma',
    'SH01-Cong',
    'SH01-XiangCai',
    'SH01-SuanMo',
    'SH01-JiangMo',
    'SH01-JiaDanJiaCai',
    'SH01-TangDi',
    'SH01-PiBaoHou'
  )
WHERE p.store_id = (
    SELECT id
    FROM stores
    WHERE store_no = 'SH01'
  )
  AND c.category_no = 'SH01-ChaoShouShuiJiao'
  AND NOT EXISTS (
    SELECT 1
    FROM product_option_groups pog
    WHERE pog.product_id = p.id
      AND pog.group_id = g.id
  );
-- 5.3 汤圆荷包蛋：甜度 + 荷包蛋熟度 + 汤圆数量
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id,
  g.id,
  g.sort_order
FROM products p
  JOIN option_groups g ON g.code IN (
    'SH01-TianDu',
    'SH01-ShuDu',
    'SH01-TangYuanShuLiang'
  )
WHERE p.store_id = (
    SELECT id
    FROM stores
    WHERE store_no = 'SH01'
  )
  AND p.product_no = 'SH01-TangYuan-TangYuanHeBaoDan'
  AND NOT EXISTS (
    SELECT 1
    FROM product_option_groups pog
    WHERE pog.product_id = p.id
      AND pog.group_id = g.id
  );
-- 5.4 红糖小汤圆：甜度 + 汤圆数量
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id,
  g.id,
  g.sort_order
FROM products p
  JOIN option_groups g ON g.code IN ('SH01-TianDu', 'SH01-TangYuanShuLiang')
WHERE p.store_id = (
    SELECT id
    FROM stores
    WHERE store_no = 'SH01'
  )
  AND p.product_no = 'SH01-TangYuan-HongTangXiaoTangYuan'
  AND NOT EXISTS (
    SELECT 1
    FROM product_option_groups pog
    WHERE pog.product_id = p.id
      AND pog.group_id = g.id
  );
-- 5.5 包子·点心：包子个数 + 是否加热
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id,
  g.id,
  g.sort_order
FROM products p
  JOIN product_categories c ON c.id = p.category_id
  JOIN option_groups g ON g.code IN ('SH01-BaoZiGeShu', 'SH01-ShiFouJiaRe')
WHERE p.store_id = (
    SELECT id
    FROM stores
    WHERE store_no = 'SH01'
  )
  AND c.category_no = 'SH01-BaoZiDianXin'
  AND NOT EXISTS (
    SELECT 1
    FROM product_option_groups pog
    WHERE pog.product_id = p.id
      AND pog.group_id = g.id
  );
-- 添加桌号（WM=外带虚拟通道，非实体桌；堂食用 A01/A02）
INSERT INTO `smart_noodle_house`.`tables` (
    `id`,
    `store_id`,
    `table_no`,
    `table_name`,
    `qr_token`,
    `wechat_qr_url`,
    `alipay_qr_url`,
    `status`,
    `created_at`,
    `updated_at`
  )
VALUES 
(
    1,
    1,
    'WM',
    '外带自取',
    'M7v-8H_ZLOdUXsR9UYPH7Umj-ZU0Z4JuEijy5X_w3kg',
    'weixin://dl/business/?appid=wxd0964c5d02b1549d&path=pages/index/index&query=token%3DM7v-8H_ZLOdUXsR9UYPH7Umj-ZU0Z4JuEijy5X_w3kg%26channel%3Dwechat&env_version=develop',
    'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages%2Findex%2Findex&query=token%3DM7v-8H_ZLOdUXsR9UYPH7Umj-ZU0Z4JuEijy5X_w3kg%26channel%3Dalipay',
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    2,
    1,
    'A01',
    '1号桌',
    '6jO4K28UbqPozFWvtT0uqPpR5WWwD3030ir2ORwQYWs',
    'weixin://dl/business/?appid=wxd0964c5d02b1549d&path=pages/index/index&query=token%3D6jO4K28UbqPozFWvtT0uqPpR5WWwD3030ir2ORwQYWs%26channel%3Dwechat&env_version=develop',
    'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages%2Findex%2Findex&query=token%3D6jO4K28UbqPozFWvtT0uqPpR5WWwD3030ir2ORwQYWs%26channel%3Dalipay',
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  ),
  (
    3,
    1,
    'A02',
    '2号桌',
    'wJYZpvc1g-FqvZ7KE-XtFaS_M115B6KCcm93Cav6VbA',
    'weixin://dl/business/?appid=wxd0964c5d02b1549d&path=pages/index/index&query=token%3DwJYZpvc1g-FqvZ7KE-XtFaS_M115B6KCcm93Cav6VbA%26channel%3Dwechat&env_version=develop',
    'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages%2Findex%2Findex&query=token%3DwJYZpvc1g-FqvZ7KE-XtFaS_M115B6KCcm93Cav6VbA%26channel%3Dalipay',
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- END PRODUCTION SEED

SET FOREIGN_KEY_CHECKS = 1;

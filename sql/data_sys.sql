-- =============================================================================
-- 系统必要种子（平台运行必需）
-- 前置：已执行 sql/struct.sql
-- 顺序：struct.sql → data_sys.sql → data_custom.sql（门店经营数据）→ 可选 data_test.sql
-- =============================================================================
USE `smart_noodle_house`;
-- 设置字符集
SET NAMES utf8mb4;
-- 禁用外键检查
SET FOREIGN_KEY_CHECKS = 0;
-- 清空所有表
TRUNCATE TABLE `ab_assignments`;
TRUNCATE TABLE `ab_experiments`;
TRUNCATE TABLE `balance_ledger`;
TRUNCATE TABLE `cameras`;
TRUNCATE TABLE `cashier_shifts`;
TRUNCATE TABLE `category_sort_schedule_items`;
TRUNCATE TABLE `category_sort_schedules`;
TRUNCATE TABLE `coupon_templates`;
TRUNCATE TABLE `customer_alipay_accounts`;
TRUNCATE TABLE `customer_behavior_events`;
TRUNCATE TABLE `customer_coupons`;
TRUNCATE TABLE `customer_favorite_products`;
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
TRUNCATE TABLE `invoice_requests`;
TRUNCATE TABLE `member_levels`;
TRUNCATE TABLE `operation_logs`;
TRUNCATE TABLE `option_groups`;
TRUNCATE TABLE `options`;
TRUNCATE TABLE `order_item_options`;
TRUNCATE TABLE `order_items`;
TRUNCATE TABLE `order_milestone_rewards`;
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
TRUNCATE TABLE `processed_messages`;
TRUNCATE TABLE `print_jobs`;
TRUNCATE TABLE `product_categories`;
TRUNCATE TABLE `product_feedbacks`;
TRUNCATE TABLE `product_images`;
TRUNCATE TABLE `product_option_groups`;
TRUNCATE TABLE `product_taste_profiles`;
TRUNCATE TABLE `products`;
TRUNCATE TABLE `recharge_orders`;
TRUNCATE TABLE `recharge_packages`;
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
SELECT 'SUPER_ADMIN',
    '超级管理员',
    '跨店全部权限'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `roles`
        WHERE `code` = 'SUPER_ADMIN'
    );
INSERT INTO `roles` (`code`, `name`, `description`)
SELECT 'STORE_ADMIN',
    '门店管理员',
    '本店运营（含本店业务设置与退款；不含平台级系统配置）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `roles`
        WHERE `code` = 'STORE_ADMIN'
    );
INSERT INTO `roles` (`code`, `name`, `description`)
SELECT 'CASHIER',
    '收银员',
    '订单、支付与收银点餐（不可退款）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `roles`
        WHERE `code` = 'CASHIER'
    );
INSERT INTO `roles` (`code`, `name`, `description`)
SELECT 'KITCHEN',
    '厨房',
    'KDS 出餐（不可退款/不可审外卖退款）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `roles`
        WHERE `code` = 'KITCHEN'
    );
INSERT INTO `roles` (`code`, `name`, `description`)
SELECT 'OPERATOR',
    '运营',
    '商品与数据（不可退款）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `roles`
        WHERE `code` = 'OPERATOR'
    );
-- 二、权限点
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'product:read',
    '查看商品',
    'product'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'product:read'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'product:create',
    '创建商品',
    'product'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'product:create'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'product:update',
    '更新商品',
    'product'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'product:update'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'product:delete',
    '删除商品',
    'product'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'product:delete'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'order:read',
    '查看订单',
    'order'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'order:read'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'order:update',
    '更新订单',
    'order'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'order:update'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'customer:read',
    '查看顾客',
    'customer'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'customer:read'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'customer:manage',
    '管理全品牌会员等级',
    'customer'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'customer:manage'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'customer:write',
    '合并顾客',
    'customer'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'customer:write'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'camera:read',
    '查看摄像头',
    'camera'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'camera:read'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'camera:manage',
    '管理摄像头',
    'camera'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'camera:manage'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'welcome:manage',
    '管理迎宾',
    'welcome'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'welcome:manage'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'payment:read',
    '查看支付',
    'payment'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'payment:read'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'payment:refund',
    '支付与外卖退款',
    'payment'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'payment:refund'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'table:manage',
    '管理桌台',
    'table'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'table:manage'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'user:read',
    '查看员工',
    'user'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'user:read'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'user:manage',
    '管理员工',
    'user'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'user:manage'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'system:manage',
    '系统管理',
    'system'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'system:manage'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'settings:store',
    '门店业务设置',
    'system'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'settings:store'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'coupon:read',
    '查看优惠券',
    'coupon'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'coupon:read'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'coupon:manage',
    '管理优惠券',
    'coupon'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'coupon:manage'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'audit:read',
    '查看操作日志',
    'audit'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'audit:read'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'wallet:read',
    '查看储值',
    'wallet'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'wallet:read'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'wallet:manage',
    '管理储值档位与调账',
    'wallet'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'wallet:manage'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'invoice:manage',
    '管理电子发票',
    'order'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'invoice:manage'
    );
-- 三、角色权限矩阵
INSERT INTO `role_permissions` (`role_id`, `permission_id`)
SELECT `r`.`id`,
    `p`.`id`
FROM `roles` r
    CROSS JOIN `permissions` p
WHERE `r`.`code` = 'SUPER_ADMIN'
    AND NOT EXISTS (
        SELECT 1
        FROM `role_permissions` rp
        WHERE `rp`.`role_id` = `r`.`id`
            AND `rp`.`permission_id` = `p`.`id`
    );
INSERT INTO `role_permissions` (`role_id`, `permission_id`)
SELECT `r`.`id`,
    `p`.`id`
FROM `roles` r
    JOIN `permissions` p ON `p`.`code` IN (
        'product:read',
        'product:create',
        'product:update',
        'product:delete',
        'order:read',
        'order:update',
        'customer:read',
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
        'audit:read',
        'wallet:read',
        'wallet:manage',
        'settings:store',
        'invoice:manage'
    )
WHERE `r`.`code` = 'STORE_ADMIN'
    AND NOT EXISTS (
        SELECT 1
        FROM `role_permissions` rp
        WHERE `rp`.`role_id` = `r`.`id`
            AND `rp`.`permission_id` = `p`.`id`
    );
INSERT INTO `role_permissions` (`role_id`, `permission_id`)
SELECT `r`.`id`,
    `p`.`id`
FROM `roles` r
    JOIN `permissions` p ON `p`.`code` IN ('order:read', 'order:update')
WHERE `r`.`code` = 'KITCHEN'
    AND NOT EXISTS (
        SELECT 1
        FROM `role_permissions` rp
        WHERE `rp`.`role_id` = `r`.`id`
            AND `rp`.`permission_id` = `p`.`id`
    );
INSERT INTO `role_permissions` (`role_id`, `permission_id`)
SELECT `r`.`id`,
    `p`.`id`
FROM `roles` r
    JOIN `permissions` p ON `p`.`code` IN (
        'order:read',
        'order:update',
        'payment:read',
        'customer:read',
        'product:read',
        'wallet:read'
    )
WHERE `r`.`code` = 'CASHIER'
    AND NOT EXISTS (
        SELECT 1
        FROM `role_permissions` rp
        WHERE `rp`.`role_id` = `r`.`id`
            AND `rp`.`permission_id` = `p`.`id`
    );
INSERT INTO `role_permissions` (`role_id`, `permission_id`)
SELECT `r`.`id`,
    `p`.`id`
FROM `roles` r
    JOIN `permissions` p ON `p`.`code` IN (
        'product:read',
        'product:create',
        'product:update',
        'product:delete',
        'order:read',
        'customer:read',
        'payment:read',
        'wallet:read'
    )
WHERE `r`.`code` = 'OPERATOR'
    AND NOT EXISTS (
        SELECT 1
        FROM `role_permissions` rp
        WHERE `rp`.`role_id` = `r`.`id`
            AND `rp`.`permission_id` = `p`.`id`
    );
-- 四、会员等级
INSERT INTO `member_levels` (`name`, `min_points`, `discount_rate`)
SELECT '普通会员',
    0,
    1.00
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `member_levels`
        WHERE `name` = '普通会员'
    );
INSERT INTO `member_levels` (`name`, `min_points`, `discount_rate`)
SELECT '银卡会员',
    200,
    0.98
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `member_levels`
        WHERE `name` = '银卡会员'
    );
INSERT INTO `member_levels` (`name`, `min_points`, `discount_rate`)
SELECT '金卡会员',
    500,
    0.95
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `member_levels`
        WHERE `name` = '金卡会员'
    );
INSERT INTO `member_levels` (`name`, `min_points`, `discount_rate`)
SELECT '钻石会员',
    1000,
    0.90
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `member_levels`
        WHERE `name` = '钻石会员'
    );
-- 五、券模板（VOUCHER 一次性核销；应付须≥面额）
-- 总部新客券名须与系统参数 / 总店 welcome_coupon_name 一致（「新客满30减5」）
INSERT INTO `coupon_templates` (
        `name`,
        `coupon_type`,
        `discount_value`,
        `min_order_amount`,
        `valid_days`
    )
SELECT '新客满30减5',
    'FIXED',
    5.00,
    30.00,
    30
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '新客满30减5'
    );
INSERT INTO `coupon_templates` (
        `name`,
        `coupon_type`,
        `discount_value`,
        `min_order_amount`,
        `valid_days`
    )
SELECT '满50享打9折',
    'PERCENT',
    0.90,
    50.00,
    30
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '满50享打9折'
    );
INSERT INTO `coupon_templates` (
        `name`,
        `coupon_type`,
        `discount_value`,
        `min_order_amount`,
        `valid_days`
    )
SELECT '10元代金券',
    'VOUCHER',
    10.00,
    0.00,
    30
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '10元代金券'
    );
INSERT INTO `coupon_templates` (
        `name`,
        `coupon_type`,
        `discount_value`,
        `min_order_amount`,
        `valid_days`
    )
SELECT '满10单免1元',
    'FIXED',
    1.00,
    0.00,
    30
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '满10单免1元'
    );
INSERT INTO `coupon_templates` (
        `name`,
        `coupon_type`,
        `discount_value`,
        `min_order_amount`,
        `valid_days`
    )
SELECT '满20单免3元',
    'FIXED',
    3.00,
    0.00,
    30
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '满20单免3元'
    );
INSERT INTO `coupon_templates` (
        `name`,
        `coupon_type`,
        `discount_value`,
        `min_order_amount`,
        `valid_days`
    )
SELECT '满50单免8元',
    'FIXED',
    8.00,
    0.00,
    30
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '满50单免8元'
    );
INSERT INTO `coupon_templates` (
        `name`,
        `coupon_type`,
        `discount_value`,
        `min_order_amount`,
        `valid_days`
    )
SELECT '满100单免12元',
    'FIXED',
    12.00,
    0.00,
    30
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '满100单免12元'
    );
INSERT INTO `coupon_templates` (
        `name`,
        `coupon_type`,
        `discount_value`,
        `min_order_amount`,
        `valid_days`
    )
SELECT '满200单免30元',
    'FIXED',
    30.00,
    0.00,
    30
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '满200单免30元'
    );
-- 五-B、储值档位（射洪友好：充50/100/200）
INSERT INTO `recharge_packages` (
        `store_id`,
        `name`,
        `pay_amount`,
        `gift_amount`,
        `sort_order`,
        `status`
    )
SELECT NULL,
    '充50得55',
    50.00,
    5.00,
    0,
    1
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `recharge_packages`
        WHERE `store_id` IS NULL
            AND `name` = '充50得55'
    );
INSERT INTO `recharge_packages` (
        `store_id`,
        `name`,
        `pay_amount`,
        `gift_amount`,
        `sort_order`,
        `status`
    )
SELECT NULL,
    '充100得120',
    100.00,
    20.00,
    1,
    1
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `recharge_packages`
        WHERE `store_id` IS NULL
            AND `name` = '充100得120'
    );
INSERT INTO `recharge_packages` (
        `store_id`,
        `name`,
        `pay_amount`,
        `gift_amount`,
        `sort_order`,
        `status`
    )
SELECT NULL,
    '充200得250',
    200.00,
    50.00,
    2,
    1
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `recharge_packages`
        WHERE `store_id` IS NULL
            AND `name` = '充200得250'
    );
-- 七、系统参数
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'invoice_enabled',
    'false',
    '电子发票（小程序开票入口，默认关闭）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'invoice_enabled'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'order_payment_timeout_minutes',
    '15',
    '订单支付超时分钟数'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'order_payment_timeout_minutes'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'visitor_data_retention_days',
    '7',
    '匿名访客数据保留天数'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'visitor_data_retention_days'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'welcome_cooldown_seconds',
    '60',
    '迎宾冷却秒数'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'welcome_cooldown_seconds'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'face_match_threshold',
    '0.75',
    '人脸匹配阈值'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'face_match_threshold'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'face_confirm_frames',
    '5',
    '人脸确认帧数'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'face_confirm_frames'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'unknown_visitor_capture_enabled',
    'true',
    '未知访客抓拍（默认开启）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'unknown_visitor_capture_enabled'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'operation_log_retention_days',
    '60',
    '操作日志保留天数'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'operation_log_retention_days'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'kitchen_auto_complete_minutes',
    '30',
    '厨房 KDS 未处理自动出餐（分钟，0=关闭）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'kitchen_auto_complete_minutes'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'enable_customer_display',
    'false',
    '大厅叫号屏开关'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'enable_customer_display'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'display_screen_token',
    '',
    '叫号屏访问令牌（空则回退 .env DISPLAY_SCREEN_TOKEN）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'display_screen_token'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'delivery_auto_confirm',
    'false',
    '新外卖单是否自动向平台 confirm'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'delivery_auto_confirm'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'welcome_coupon_name',
    '新客满30减5',
    '注册赠券模板名'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'welcome_coupon_name'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'enable_alipay_avatar',
    'true',
    '支付宝头像能力'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'enable_alipay_avatar'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'enable_alipay_nickname',
    'true',
    '支付宝昵称能力'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'enable_alipay_nickname'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'enable_alipay_phone_bind',
    'false',
    '支付宝手机号绑定'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'enable_alipay_phone_bind'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'enable_wx_avatar',
    'true',
    '微信头像能力'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'enable_wx_avatar'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'enable_wx_nickname',
    'true',
    '微信昵称能力'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'enable_wx_nickname'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'enable_wx_phone_bind',
    'false',
    '微信手机号绑定'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'enable_wx_phone_bind'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'enable_face_welcome',
    'false',
    '小程序人脸迎宾入口（默认关闭）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'enable_face_welcome'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'show_menu_search',
    'false',
    '点餐页是否显示搜索菜品'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'show_menu_search'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'show_menu_smart_taste',
    'false',
    '点餐页是否显示智能口味'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'show_menu_smart_taste'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'show_menu_guess_you_like',
    'false',
    '点餐页是否显示猜你喜欢'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'show_menu_guess_you_like'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'hide_member_empty_wallet',
    'true',
    '会员中心无可用券时隐藏券区'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'hide_member_empty_wallet'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'max_upload_size_mb',
    '5',
    '单文件上传上限 MB'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'max_upload_size_mb'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'jwt_expire_minutes',
    '1440',
    '员工 Token 有效期（分钟）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'jwt_expire_minutes'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'customer_jwt_expire_minutes',
    '1440',
    '顾客 Token 有效期（分钟）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'customer_jwt_expire_minutes'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'webhook_ip_allowlist',
    '',
    'Webhook IP 白名单'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'webhook_ip_allowlist'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'webhook_require_allowlist',
    'false',
    '强制 Webhook 白名单'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'webhook_require_allowlist'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'trusted_proxy_ips',
    '127.0.0.1, ::1',
    '可信反代 IP'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'trusted_proxy_ips'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'h5_public_base_url',
    '',
    'H5 对外域名'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'h5_public_base_url'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'dev_lan_host',
    '',
    '开发机局域网 IP'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'dev_lan_host'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'mini_program_env_version',
    'auto',
    '小程序码版本'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'mini_program_env_version'
    );
-- true/false
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'xpyun_enabled',
    'false',
    '厨房打印总开关'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'xpyun_enabled'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'printer_mode',
    'local',
    '打印机模式'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'printer_mode'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'kitchen_ticket_order_no',
    'barcode',
    '厨房票订单号样式 barcode/text/off'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'kitchen_ticket_order_no'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'xpyun_print_copies',
    '1',
    '小票打印份数'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'xpyun_print_copies'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'xpyun_voice',
    '0',
    '云打印语音 0~3'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'xpyun_voice'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'printer_host',
    '192.168.1.150',
    '打印机 IP'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'printer_host'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'printer_port',
    '9100',
    '打印机端口'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'printer_port'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'wechat_subscribe_template_order_ready',
    '',
    '微信出餐订阅模板'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'wechat_subscribe_template_order_ready'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'allowed_image_extensions',
    'jpg, jpeg, png, webp, gif',
    '允许的图片扩展名'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'allowed_image_extensions'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'app_timezone',
    'Asia/Shanghai',
    '业务时区'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'app_timezone'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'delivery_env_fallback_store_id',
    '0',
    '外卖 .env 兜底门店'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'delivery_env_fallback_store_id'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'preference_scene_enabled',
    'true',
    '口味场景加权（季节/同星期同餐段）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'preference_scene_enabled'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'weather_pref_enabled',
    'true',
    '天气口味加权（需门店经纬度）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'weather_pref_enabled'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'night_batch_enabled',
    'true',
    '关店夜间批处理开关'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'night_batch_enabled'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'night_batch_window_start_hour',
    '2',
    '夜间批处理开始整点（含）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'night_batch_window_start_hour'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'night_batch_window_end_hour',
    '5',
    '夜间批处理结束整点（不含）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'night_batch_window_end_hour'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'night_batch_sales_lookback_days',
    '7',
    '热销统计天数'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'night_batch_sales_lookback_days'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'night_batch_hot_top_n',
    '5',
    '热销 Top N'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'night_batch_hot_top_n'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'night_batch_recommend_top_n',
    '8',
    '推荐 Top N'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'night_batch_recommend_top_n'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'night_batch_taste_customer_limit',
    '2000',
    '夜间口味学习人数上限'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'night_batch_taste_customer_limit'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'night_batch_taste_lookback_days',
    '3',
    '夜间口味学习天数'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'night_batch_taste_lookback_days'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'night_batch_category_sort_enabled',
    'true',
    '夜间按各时段销量自动刷新分类排序'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'night_batch_category_sort_enabled'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'night_batch_category_sort_lookback_days',
    '30',
    '分类时段自动排序统计天数（越长越稳）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'night_batch_category_sort_lookback_days'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'night_batch_category_sort_soft_qty',
    '100',
    '该时段累计销量达此值时完全按销量排序'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'night_batch_category_sort_soft_qty'
    );
-- 八、校正（幂等）：非店长角色不得持有 payment:refund；店长可管本店业务设置
-- 会员等级折扣全品牌共用，店长不可改（customer:manage 仅超管）
DELETE rp
FROM `role_permissions` rp
    JOIN `roles` r ON `r`.`id` = `rp`.`role_id`
    JOIN `permissions` p ON `p`.`id` = `rp`.`permission_id`
WHERE `r`.`code` IN ('CASHIER', 'KITCHEN', 'OPERATOR')
    AND `p`.`code` = 'payment:refund';
DELETE rp
FROM `role_permissions` rp
    JOIN `roles` r ON `r`.`id` = `rp`.`role_id`
    JOIN `permissions` p ON `p`.`id` = `rp`.`permission_id`
WHERE `r`.`code` = 'STORE_ADMIN'
    AND `p`.`code` = 'customer:manage';
UPDATE `permissions`
SET `name` = '支付与外卖退款'
WHERE `code` = 'payment:refund';
UPDATE `permissions`
SET `name` = '管理全品牌会员等级'
WHERE `code` = 'customer:manage';
UPDATE `permissions`
SET `name` = '合并顾客'
WHERE `code` = 'customer:write';
INSERT INTO `role_permissions` (`role_id`, `permission_id`)
SELECT `r`.`id`,
    `p`.`id`
FROM `roles` r
    JOIN `permissions` p ON `p`.`code` = 'customer:write'
WHERE `r`.`code` = 'SUPER_ADMIN'
    AND NOT EXISTS (
        SELECT 1
        FROM `role_permissions` rp
        WHERE `rp`.`role_id` = `r`.`id`
            AND `rp`.`permission_id` = `p`.`id`
    );
UPDATE `roles`
SET `description` = '本店运营（含本店业务设置与退款；不含平台级系统配置）'
WHERE `code` = 'STORE_ADMIN';
INSERT INTO `role_permissions` (`role_id`, `permission_id`)
SELECT `r`.`id`,
    `p`.`id`
FROM `roles` r
    JOIN `permissions` p ON `p`.`code` = 'settings:store'
WHERE `r`.`code` IN ('SUPER_ADMIN', 'STORE_ADMIN')
    AND NOT EXISTS (
        SELECT 1
        FROM `role_permissions` rp
        WHERE `rp`.`role_id` = `r`.`id`
            AND `rp`.`permission_id` = `p`.`id`
    );
SELECT (
        SELECT COUNT(*)
        FROM `roles`
    ) AS roles,
    (
        SELECT COUNT(*)
        FROM `permissions`
    ) AS permissions;
-- 平台超级管理员（生产请立即执行 scripts/初始化管理员.py 改强密码）
INSERT INTO `users` (
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
SELECT 'admin',
    '$2b$12$38dPQAo/7seG5MQ9uCaLg.eysxss7vHFFOYSORWyAO5sNflaFgaQm',
    '系统管理员',
    `r`.`id`,
    NULL,
    0,
    1,
    NULL,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
FROM `roles` r
WHERE `r`.`code` = 'SUPER_ADMIN'
    AND NOT EXISTS (
        SELECT 1
        FROM `users` u
        WHERE `u`.`username` = 'admin'
    );
-- END SYSTEM SEED
SET FOREIGN_KEY_CHECKS = 1;
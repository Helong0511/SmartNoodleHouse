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
TRUNCATE TABLE `campaign_attributions`;
TRUNCATE TABLE `cashier_shifts`;
TRUNCATE TABLE `category_sort_schedule_items`;
TRUNCATE TABLE `category_sort_schedules`;
TRUNCATE TABLE `coupon_templates`;
TRUNCATE TABLE `customer_alipay_accounts`;
TRUNCATE TABLE `customer_behavior_events`;
TRUNCATE TABLE `customer_coupons`;
TRUNCATE TABLE `customer_favorite_products`;
TRUNCATE TABLE `customer_identities`;
TRUNCATE TABLE `customer_lifecycle`;
TRUNCATE TABLE `customer_preferences`;
TRUNCATE TABLE `customer_segment_members`;
TRUNCATE TABLE `customer_segments`;
TRUNCATE TABLE `customer_social_identities`;
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
TRUNCATE TABLE `marketing_campaigns`;
TRUNCATE TABLE `marketing_message_logs`;
TRUNCATE TABLE `marketing_rules`;
TRUNCATE TABLE `marketing_tasks`;
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
TRUNCATE TABLE `point_exchange_options`;
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
TRUNCATE TABLE `queue_tickets`;
TRUNCATE TABLE `recharge_orders`;
TRUNCATE TABLE `recharge_packages`;
TRUNCATE TABLE `role_permissions`;
TRUNCATE TABLE `roles`;
TRUNCATE TABLE `social_benefit_redemptions`;
TRUNCATE TABLE `social_benefit_rules`;
TRUNCATE TABLE `social_identity_types`;
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
    '餐品与数据（不可退款）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `roles`
        WHERE `code` = 'OPERATOR'
    );
-- 二、权限点
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'product:read',
    '查看餐品',
    'product'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'product:read'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'product:create',
    '创建餐品',
    'product'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'product:create'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'product:update',
    '更新餐品',
    'product'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'product:update'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'product:delete',
    '删除餐品',
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
    '改顾客档案与合并',
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
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'customer:pii',
    '查看顾客完整手机号等 PII',
    'customer'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'customer:pii'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'product:cost:read',
    '查看餐品成本价',
    'product'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'product:cost:read'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'role:delegate',
    '临时授权/委派权限',
    'user'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'role:delegate'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'reconcile:read',
    '查看支付对账批次与差异',
    'payment'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'reconcile:read'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'reconcile:manage',
    '导入渠道账单并执行对账',
    'payment'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'reconcile:manage'
    );
INSERT INTO `permissions` (`code`, `name`, `module`)
SELECT 'archive:manage',
    '执行历史订单冷库归档',
    'order'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `permissions`
        WHERE `code` = 'archive:manage'
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
        'customer:write',
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
        'invoice:manage',
        'customer:pii',
        'product:cost:read',
        'role:delegate',
        'reconcile:read',
        'reconcile:manage',
        'archive:manage'
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
    0.95
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `member_levels`
        WHERE `name` = '银卡会员'
    );
INSERT INTO `member_levels` (`name`, `min_points`, `discount_rate`)
SELECT '金卡会员',
    500,
    0.92
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
-- 会员折扣对齐运营表：银卡 0.98→0.95；金卡/钻石与种子一致（幂等纠偏）
UPDATE `member_levels` SET `discount_rate` = 0.95 WHERE `name` = '银卡会员' AND `discount_rate` <> 0.95;
UPDATE `member_levels` SET `discount_rate` = 0.92 WHERE `name` = '金卡会员' AND `discount_rate` <> 0.92;
UPDATE `member_levels` SET `discount_rate` = 0.90 WHERE `name` = '钻石会员' AND `discount_rate` <> 0.90;
-- 五、券模板（VOUCHER 一次性核销；应付须≥面额）
-- 总部新客券名须与系统参数 / 总店 welcome_coupon_name 一致（「新客满20减3」）
-- 废弃旧模板「新客满30减5」：停用，避免误选；历史已发券仍可核销
UPDATE `coupon_templates`
SET `status` = 0
WHERE `name` = '新客满30减5'
  AND `status` <> 0;
INSERT INTO `coupon_templates` (
        `name`,
        `coupon_type`,
        `discount_value`,
        `min_order_amount`,
        `valid_days`
    )
SELECT '新客满20减3',
    'FIXED',
    3.00,
    20.00,
    30
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '新客满20减3'
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
SELECT '满10单免2元',
    'FIXED',
    2.00,
    0.00,
    30
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '满10单免2元'
    );
-- 废弃旧「满10单免1元」：停用，并把里程碑引用切到「满10单免2元」
UPDATE `coupon_templates` SET `status` = 0 WHERE `name` = '满10单免1元' AND `status` <> 0;
UPDATE `order_milestone_rewards` `omr`
INNER JOIN `coupon_templates` `old` ON `old`.`id` = `omr`.`template_id` AND `old`.`name` = '满10单免1元'
INNER JOIN `coupon_templates` `new` ON `new`.`name` = '满10单免2元'
SET `omr`.`template_id` = `new`.`id`
WHERE `omr`.`threshold_orders` = 10;
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
-- 五-A2、营销场景券模板（生日/沉睡唤醒/爱心日）
INSERT INTO `coupon_templates` (
        `name`,
        `coupon_type`,
        `discount_value`,
        `min_order_amount`,
        `valid_days`
    )
SELECT '生日免费加蛋',
    'FIXED',
    3.00,
    0.00,
    7
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '生日免费加蛋'
    );
INSERT INTO `coupon_templates` (
        `name`,
        `coupon_type`,
        `discount_value`,
        `min_order_amount`,
        `valid_days`
    )
SELECT '沉睡唤醒满20减3',
    'FIXED',
    3.00,
    20.00,
    30
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '沉睡唤醒满20减3'
    );
INSERT INTO `coupon_templates` (
        `name`,
        `coupon_type`,
        `discount_value`,
        `min_order_amount`,
        `valid_days`
    )
SELECT '沉睡唤醒满15减5',
    'FIXED',
    5.00,
    15.00,
    30
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '沉睡唤醒满15减5'
    );
INSERT INTO `coupon_templates` (
        `name`,
        `coupon_type`,
        `discount_value`,
        `min_order_amount`,
        `valid_days`
    )
SELECT '爱心日免费豆浆',
    'VOUCHER',
    3.00,
    0.00,
    1
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `coupon_templates`
        WHERE `name` = '爱心日免费豆浆'
    );
-- 五-B、储值档位（平台总部档，store_id=NULL；射洪友好：充50/100/200）
-- 纠偏：历史种子曾误写 store_id=1。若总部档尚缺且全库仅有一条同名门店档，则升为总部。
UPDATE `recharge_packages` AS `rp`
INNER JOIN (
        SELECT MIN(`id`) AS `id`
        FROM `recharge_packages`
        WHERE `store_id` IS NOT NULL
            AND `name` IN ('充50得55', '充100得120', '充200得250')
            AND `name` NOT IN (
                SELECT `name`
                FROM `recharge_packages`
                WHERE `store_id` IS NULL
            )
        GROUP BY `name`
        HAVING COUNT(*) = 1
    ) AS `t` ON `t`.`id` = `rp`.`id`
SET `rp`.`store_id` = NULL;
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
    '电子发票（小程序开票入口，默认关闭，可按门店覆盖）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'invoice_enabled'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'shared_cart_enabled',
    'false',
    '同桌共享购物车（堂食多人同一份草稿车，默认关闭，可按门店覆盖）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'shared_cart_enabled'
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
SELECT 'welcome_greeting_prefix',
    '尊贵的 {称呼} {会员等级}，欢迎您再次光临{店名}',
    '人脸迎宾语模板；占位符 {称呼}{会员等级}{店名}{口味}；可按门店覆盖'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'welcome_greeting_prefix'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'welcome_preference_template',
    '',
    '有口味爱好时追加的迎宾附句；可按门店覆盖'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'welcome_preference_template'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'welcome_product_template',
    '',
    '有智能推荐餐品时追加的迎宾附句；默认空；可按门店覆盖'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'welcome_product_template'
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
SELECT 'camera_speaker_tts_enabled',
    'false',
    '摄像头播放迎宾语音（默认关闭；海康对讲扬声器）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'camera_speaker_tts_enabled'
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
    '新客满20减3',
    '注册赠券模板名'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'welcome_coupon_name'
    );
-- 全局参数：旧「新客满30减5」改为「新客满20减3」
UPDATE `system_settings`
SET `setting_value` = '新客满20减3'
WHERE `setting_key` = 'welcome_coupon_name'
  AND `setting_value` = '新客满30减5';
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'welcome_bundle_template_ids',
    '',
    '新人礼包模板ID JSON数组；空则用 welcome_coupon_name'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'welcome_bundle_template_ids'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'birthday_benefit_mode',
    'points_and_coupon',
    '生日优惠方式：off/points/coupon/points_and_coupon'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'birthday_benefit_mode'
    );
-- 运营表：生日由「仅双倍积分」升级为积分+赠券
UPDATE `system_settings`
SET `setting_value` = 'points_and_coupon'
WHERE `setting_key` = 'birthday_benefit_mode'
  AND `setting_value` IN ('points', 'coupon', '');
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'birthday_points_multiplier',
    '2',
    '生日积分倍数（相对实付1元=1分）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'birthday_points_multiplier'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'birthday_coupon_name',
    '生日免费加蛋',
    '生日赠券模板名（空=不发）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'birthday_coupon_name'
    );
UPDATE `system_settings`
SET `setting_value` = '生日免费加蛋'
WHERE `setting_key` = 'birthday_coupon_name'
  AND (`setting_value` IS NULL OR TRIM(`setting_value`) = '');
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'sleep_wake_coupon_name_30',
    '沉睡唤醒满20减3',
    '沉睡30-90天唤醒券模板名（空=关）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'sleep_wake_coupon_name_30'
    );
UPDATE `system_settings`
SET `setting_value` = '沉睡唤醒满20减3'
WHERE `setting_key` = 'sleep_wake_coupon_name_30'
  AND (`setting_value` IS NULL OR TRIM(`setting_value`) = '');
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'sleep_wake_coupon_name_90',
    '沉睡唤醒满15减5',
    '沉睡>=90天唤醒券模板名（空=关）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'sleep_wake_coupon_name_90'
    );
UPDATE `system_settings`
SET `setting_value` = '沉睡唤醒满15减5'
WHERE `setting_key` = 'sleep_wake_coupon_name_90'
  AND (`setting_value` IS NULL OR TRIM(`setting_value`) = '');
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'balance_low_threshold',
    '20',
    '储值余额低于该值提醒（0=关）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'balance_low_threshold'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'enable_social_benefit',
    'true',
    '社会贡献公益开关'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'enable_social_benefit'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'enable_marketing_campaigns',
    'true',
    '营销活动开关（可按门店覆盖）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'enable_marketing_campaigns'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'marketing_weekly_cap',
    '3',
    '营销活动每人每周发券上限'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'marketing_weekly_cap'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'attribution_window_hours',
    '48',
    '营销归因推送窗口小时'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'attribution_window_hours'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'points_exchange_enabled',
    'false',
    '积分兑换开关（开启后会员可用积分换券）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'points_exchange_enabled'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'points_expire_days',
    '365',
    '积分过期天数（距上次消费超期清零；0=永不过期）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'points_expire_days'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'wechat_subscribe_template_balance_low',
    '',
    '微信余额提醒订阅模板ID'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'wechat_subscribe_template_balance_low'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'lifecycle_thresholds_json',
    '{"new_days":7,"active_days":30,"at_risk_days":90,"dormant_days":120}',
    '生命周期天数阈值 JSON'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'lifecycle_thresholds_json'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'rfm_thresholds_json',
    '{"r_days":[7,30,60,90],"f_orders":[1,2,4,8],"m_amount":[30,80,150,300]}',
    'RFM 分档阈值 JSON'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'rfm_thresholds_json'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'love_day_soy_template_name',
    '爱心日免费豆浆',
    '爱心日豆浆券模板名（空=关）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'love_day_soy_template_name'
    );
-- 运营表：爱心日豆浆模板空值补齐
UPDATE `system_settings`
SET `setting_value` = '爱心日免费豆浆'
WHERE `setting_key` = 'love_day_soy_template_name'
  AND (`setting_value` IS NULL OR TRIM(`setting_value`) = '');
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'love_day_weekday',
    '0',
    '爱心日星期（0=周一）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'love_day_weekday'
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
SELECT 'alipay_subscribe_template_order_ready',
    '',
    '支付宝出餐订阅模板（与微信模板 ID 禁止混用）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'alipay_subscribe_template_order_ready'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'alipay_subscribe_template_balance_low',
    '',
    '支付宝余额提醒订阅模板（与微信模板 ID 禁止混用）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'alipay_subscribe_template_balance_low'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'queue_enabled',
    'false',
    '取餐排队码（小程序 config 下发，可按门店覆盖）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'queue_enabled'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'quick_remark_options',
    '不要香菜,少油少盐,不要辣,微辣,多加醋,加份米饭',
    '结算快捷备注（逗号分隔，可按门店覆盖）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'quick_remark_options'
    );
INSERT INTO `system_settings` (`setting_key`, `setting_value`, `description`)
SELECT 'pay_success_coupon_name',
    '',
    '支付成功领券模板名（空=关闭，可按门店覆盖）'
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `system_settings`
        WHERE `setting_key` = 'pay_success_coupon_name'
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
SET `name` = '改顾客档案与合并'
WHERE `code` = 'customer:write';
-- 超管与店长可改本店相关顾客档案/合并（店长范围由 API store 约束）
INSERT INTO `role_permissions` (`role_id`, `permission_id`)
SELECT `r`.`id`,
    `p`.`id`
FROM `roles` r
    JOIN `permissions` p ON `p`.`code` = 'customer:write'
WHERE `r`.`code` IN ('SUPER_ADMIN', 'STORE_ADMIN')
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
-- 社会贡献身份字典（系统必要；可扩展，小程序/POS 从本表动态拉取，勿写死三种）
INSERT INTO `social_identity_types` (
        `code`,
        `name`,
        `sort_order`,
        `status`,
        `default_verify_hints`
    )
SELECT 'ELDER',
    '长者',
    30,
    1,
    JSON_ARRAY('身份证', '老年证')
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `social_identity_types`
        WHERE `code` = 'ELDER'
    );
INSERT INTO `social_identity_types` (
        `code`,
        `name`,
        `sort_order`,
        `status`,
        `default_verify_hints`
    )
SELECT 'TEACHER',
    '教师',
    40,
    1,
    JSON_ARRAY('教师证', '工牌')
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `social_identity_types`
        WHERE `code` = 'TEACHER'
    );
INSERT INTO `social_identity_types` (
        `code`,
        `name`,
        `sort_order`,
        `status`,
        `default_verify_hints`
    )
SELECT 'VETERAN',
    '退役军人',
    80,
    1,
    JSON_ARRAY('退役军人优待证', '军官证/士兵证')
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `social_identity_types`
        WHERE `code` = 'VETERAN'
    );
INSERT INTO `social_identity_types` (
        `code`,
        `name`,
        `sort_order`,
        `status`,
        `default_verify_hints`
    )
SELECT 'SANITATION',
    '环卫工',
    10,
    1,
    JSON_ARRAY('环卫工牌', '工作服')
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `social_identity_types`
        WHERE `code` = 'SANITATION'
    );
INSERT INTO `social_identity_types` (
        `code`,
        `name`,
        `sort_order`,
        `status`,
        `default_verify_hints`
    )
SELECT 'RIDER',
    '外卖骑手',
    20,
    1,
    JSON_ARRAY('工服', '平台工牌')
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `social_identity_types`
        WHERE `code` = 'RIDER'
    );
INSERT INTO `social_identity_types` (
        `code`,
        `name`,
        `sort_order`,
        `status`,
        `default_verify_hints`
    )
SELECT 'STUDENT',
    '学生',
    50,
    1,
    JSON_ARRAY('学生证')
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `social_identity_types`
        WHERE `code` = 'STUDENT'
    );
INSERT INTO `social_identity_types` (
        `code`,
        `name`,
        `sort_order`,
        `status`,
        `default_verify_hints`
    )
SELECT 'MEDICAL',
    '医护人员',
    60,
    1,
    JSON_ARRAY('执业证', '工牌')
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `social_identity_types`
        WHERE `code` = 'MEDICAL'
    );
INSERT INTO `social_identity_types` (
        `code`,
        `name`,
        `sort_order`,
        `status`,
        `default_verify_hints`
    )
SELECT 'VOLUNTEER',
    '志愿者',
    90,
    1,
    JSON_ARRAY('志愿证', '服务证明')
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `social_identity_types`
        WHERE `code` = 'VOLUNTEER'
    );
INSERT INTO `social_identity_types` (
        `code`,
        `name`,
        `sort_order`,
        `status`,
        `default_verify_hints`
    )
SELECT 'DISABLED',
    '残障人士',
    70,
    1,
    JSON_ARRAY('残疾人证')
FROM DUAL
WHERE NOT EXISTS (
        SELECT 1
        FROM `social_identity_types`
        WHERE `code` = 'DISABLED'
    );
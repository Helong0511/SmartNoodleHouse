/*
 Navicat Premium Dump SQL
 
 Source Server         : 本地
 Source Server Type    : MySQL
 Source Server Version : 80033 (8.0.33)
 Source Host           : localhost:3306
 Source Schema         : smart_noodle_house
 
 Target Server Type    : MySQL
 Target Server Version : 80033 (8.0.33)
 File Encoding         : 65001
 
 Date: 21/09/2026 10:21:36
 */
DROP DATABASE IF EXISTS `smart_noodle_house`;
CREATE DATABASE IF NOT EXISTS `smart_noodle_house` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `smart_noodle_house`;
-- 部署前替换下方占位符；禁止把真实数据库密码提交到版本库。
CREATE USER IF NOT EXISTS 'remote' @'localhost' IDENTIFIED BY 'kb7$rL3d8!tQ';
GRANT ALL PRIVILEGES ON smart_noodle_house.* TO 'remote' @'localhost';
FLUSH PRIVILEGES;
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;
-- ----------------------------
-- Table structure for ab_assignments
-- ----------------------------
DROP TABLE IF EXISTS `ab_assignments`;
CREATE TABLE `ab_assignments` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `experiment_key` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '实验标识 → ab_experiments.experiment_key',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `bucket` varchar(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '分配到的桶编码',
  `assigned_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '分桶时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_aba_experiment_customer`(`experiment_key` ASC, `customer_id` ASC) USING BTREE,
  INDEX `idx_aba_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_aba_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = 'A/B 实验顾客分桶表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for ab_experiments
-- ----------------------------
DROP TABLE IF EXISTS `ab_experiments`;
CREATE TABLE `ab_experiments` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `experiment_key` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '实验唯一标识，如 reco_algo',
  `name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '实验名称',
  `status` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'DRAFT' COMMENT '状态：DRAFT/RUNNING/PAUSED',
  `traffic_percent` int NOT NULL DEFAULT 0 COMMENT '参与实验流量百分比 0-100；其余走默认桶',
  `default_bucket` varchar(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'v1' COMMENT '未命中流量的默认桶编码',
  `buckets` json NULL COMMENT '桶定义，如 [{\"code\":\"v1\",\"traffic\":50,\"params\":{}}]',
  `description` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '实验说明',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_abe_key`(`experiment_key` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = 'A/B 实验定义表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for cameras
-- ----------------------------
DROP TABLE IF EXISTS `cameras`;
CREATE TABLE `cameras` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '所属门店 → stores.id',
  `name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '摄像头名称/位置',
  `camera_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'USB=本机摄像头 | RTSP=网络流 | HTTP=HTTP 流',
  `source_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '设备号或 RTSP/HTTP 地址',
  `username` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '拉流用户名',
  `password_encrypted` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '加密后的拉流密码',
  `status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'OFFLINE' COMMENT 'ONLINE/OFFLINE/RECONNECTING',
  `fps` int NOT NULL DEFAULT 25 COMMENT '视频采集帧率',
  `detect_fps` int NOT NULL DEFAULT 3 COMMENT '人脸检测采样帧率（降负载）',
  `last_online_at` datetime(3) NULL DEFAULT NULL COMMENT '最近在线时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `fk_cameras_store`(`store_id` ASC) USING BTREE,
  CONSTRAINT `fk_cameras_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '门店摄像头表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of cameras
-- ----------------------------
-- ----------------------------
-- Table structure for cashier_shifts
-- ----------------------------
DROP TABLE IF EXISTS `cashier_shifts`;
CREATE TABLE `cashier_shifts` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `operator_id` bigint UNSIGNED NOT NULL COMMENT '收银员 → users.id',
  `opening_float` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '开班备用金（元）',
  `status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'OPEN' COMMENT 'OPEN=营业中 CLOSED=已交班',
  `opened_at` datetime(3) NOT NULL COMMENT '开班时间',
  `closed_at` datetime(3) NULL DEFAULT NULL COMMENT '交班时间',
  `total_cash` decimal(10, 2) NULL DEFAULT NULL COMMENT '本班现金收款',
  `total_wechat` decimal(10, 2) NULL DEFAULT NULL COMMENT '本班微信收款',
  `total_alipay` decimal(10, 2) NULL DEFAULT NULL COMMENT '本班支付宝收款',
  `total_amount` decimal(10, 2) NULL DEFAULT NULL COMMENT '本班收款合计',
  `closing_cash` decimal(10, 2) NULL DEFAULT NULL COMMENT '交班实盘现金（含备用金）',
  `cash_difference` decimal(10, 2) NULL DEFAULT NULL COMMENT '现金长短款',
  `closed_by` bigint UNSIGNED NULL DEFAULT NULL COMMENT '确认日结的管理员 → users.id',
  `remark` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '交班备注',
  `open_operator_key` bigint UNSIGNED GENERATED ALWAYS AS (
    if((`status` = _utf8mb4 'OPEN'), `operator_id`, NULL)
  ) STORED COMMENT 'OPEN 时为 operator_id，CLOSED 为 NULL；用于唯一约束' NULL,
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_cashier_shift_one_open`(`open_operator_key` ASC) USING BTREE,
  INDEX `idx_shift_store_open`(`store_id` ASC, `opened_at` ASC) USING BTREE,
  INDEX `idx_shift_operator_status`(`operator_id` ASC, `status` ASC) USING BTREE,
  CONSTRAINT `fk_shift_operator` FOREIGN KEY (`operator_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_shift_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '收银班次表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of cashier_shifts
-- ----------------------------
-- ----------------------------
-- Table structure for category_sort_schedule_items
-- ----------------------------
DROP TABLE IF EXISTS `category_sort_schedule_items`;
CREATE TABLE `category_sort_schedule_items` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `schedule_id` bigint UNSIGNED NOT NULL COMMENT '方案 → category_sort_schedules.id',
  `category_id` bigint UNSIGNED NOT NULL COMMENT '分类 → product_categories.id',
  `sort_order` int NOT NULL DEFAULT 0 COMMENT '时段内排序，越小越靠前',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_sched_cat`(`schedule_id` ASC, `category_id` ASC) USING BTREE,
  INDEX `idx_sched_item_order`(`schedule_id` ASC, `sort_order` ASC) USING BTREE,
  INDEX `fk_sched_item_cat`(`category_id` ASC) USING BTREE,
  CONSTRAINT `fk_sched_item_cat` FOREIGN KEY (`category_id`) REFERENCES `product_categories` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_sched_item_sched` FOREIGN KEY (`schedule_id`) REFERENCES `category_sort_schedules` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '时段方案内分类排序' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of category_sort_schedule_items
-- ----------------------------
-- ----------------------------
-- Table structure for category_sort_schedules
-- ----------------------------
DROP TABLE IF EXISTS `category_sort_schedules`;
CREATE TABLE `category_sort_schedules` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '方案名（如 早餐优先）',
  `start_time` time NOT NULL COMMENT '开始时间（含）',
  `end_time` time NOT NULL COMMENT '结束时间（不含）；若 start>end 表示跨午夜',
  `priority` int NOT NULL DEFAULT 0 COMMENT '重叠时取更大优先级',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_cat_sched_store`(`store_id` ASC, `status` ASC, `priority` ASC) USING BTREE,
  CONSTRAINT `fk_cat_sched_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '分类分时段排序方案' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of category_sort_schedules
-- ----------------------------
-- ----------------------------
-- Table structure for coupon_templates
-- ----------------------------
DROP TABLE IF EXISTS `coupon_templates`;
CREATE TABLE `coupon_templates` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT 'NULL=总部全店模板；非空=仅该门店 → stores.id',
  `name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '券名称（展示用）',
  `coupon_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'FIXED' COMMENT 'FIXED=满减元 | PERCENT=折扣比例 | VOUCHER=代金券抵扣',
  `discount_value` decimal(10, 4) NOT NULL COMMENT 'FIXED/VOUCHER=减免金额；PERCENT=折扣比例如0.90表示九折',
  `min_order_amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '使用门槛（按会员折后商品小计计）',
  `valid_days` int NOT NULL DEFAULT 30 COMMENT '领取/发放后有效天数',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '模板状态：1=启用 0=停用',
  `total_quota` int NULL DEFAULT NULL COMMENT 'P0-6: 发放总量上限；NULL=不限',
  `remaining` int NULL DEFAULT NULL COMMENT 'P0-6: 剩余可发量；NULL=不限',
  `store_whitelist` json NULL COMMENT 'P2-4: 总部模板可见门店白名单；NULL/空数组=全部门店',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_coupon_tmpl_store`(`store_id` ASC) USING BTREE,
  CONSTRAINT `fk_coupon_tmpl_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '优惠券/代金券模板表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of coupon_templates
-- ----------------------------
INSERT INTO `coupon_templates`
VALUES (
    1,
    NULL,
    '新客满30减5',
    'FIXED',
    5.0000,
    30.00,
    30,
    1,
    NULL,
    NULL,
    NULL,
    '2026-09-20 15:04:33.054'
  );
INSERT INTO `coupon_templates`
VALUES (
    2,
    NULL,
    '满50享打9折',
    'PERCENT',
    0.9000,
    50.00,
    30,
    1,
    NULL,
    NULL,
    NULL,
    '2026-09-20 15:04:33.058'
  );
INSERT INTO `coupon_templates`
VALUES (
    3,
    NULL,
    '10元代金券',
    'VOUCHER',
    10.0000,
    0.00,
    30,
    1,
    NULL,
    NULL,
    NULL,
    '2026-09-20 15:04:33.061'
  );
-- ----------------------------
-- Table structure for customer_alipay_accounts
-- ----------------------------
DROP TABLE IF EXISTS `customer_alipay_accounts`;
CREATE TABLE `customer_alipay_accounts` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `alipay_user_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '支付宝 user_id',
  `alipay_union_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '支付宝 union_id（如有）',
  `profile_completed` tinyint NOT NULL DEFAULT 0 COMMENT '本渠道是否完善过昵称/头像资料（≠member_status）',
  `nickname` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '本渠道展示昵称（与微信互不覆盖）',
  `avatar_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '本渠道展示头像（与微信互不覆盖）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '首次绑定时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `alipay_user_id`(`alipay_user_id` ASC) USING BTREE,
  INDEX `fk_caa_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_caa_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '支付宝账号绑定表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of customer_alipay_accounts
-- ----------------------------
-- ----------------------------
-- Table structure for customer_behavior_events
-- ----------------------------
DROP TABLE IF EXISTS `customer_behavior_events`;
CREATE TABLE `customer_behavior_events` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `event_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '客户端生成的幂等 UUID，重复上报靠它去重',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '发生门店 → stores.id',
  `event_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'EXPOSE/CLICK/CART_ADD/CART_REMOVE/ORDER_PAID/REORDER/FEEDBACK',
  `scene` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '场景：menu/recommend/cart/detail/settle/orders',
  `product_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联商品 → products.id',
  `category_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联分类 → product_categories.id',
  `ab_bucket` varchar(80) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '命中的 A/B 桶，如 reco_algo:v2',
  `payload` json NULL COMMENT '事件附加属性（不含手机号/昵称等 PII）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '事件发生时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_cbe_event_id`(`event_id` ASC) USING BTREE,
  INDEX `idx_cbe_customer_created`(`customer_id` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_cbe_store_type_created`(
    `store_id` ASC,
    `event_type` ASC,
    `created_at` ASC
  ) USING BTREE,
  INDEX `idx_cbe_created`(`created_at` ASC) USING BTREE,
  CONSTRAINT `fk_cbe_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '顾客行为事件表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of customer_behavior_events
-- ----------------------------
-- ----------------------------
-- Table structure for customer_coupons
-- ----------------------------
DROP TABLE IF EXISTS `customer_coupons`;
CREATE TABLE `customer_coupons` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '持有人 → customers.id',
  `template_id` bigint UNSIGNED NOT NULL COMMENT '券模板 → coupon_templates.id',
  `coupon_no` varchar(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '券实例编号（全局唯一）',
  `status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'AVAILABLE' COMMENT 'AVAILABLE=可用 | RESERVED=下单预占 | USED=已使用 | EXPIRED=已过期',
  `status_reason` varchar(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '状态原因：merge_dup=合并冲突作废 等',
  `obtained_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '领取/发放时间',
  `expires_at` datetime(3) NOT NULL COMMENT '过期时间',
  `used_order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '核销订单 → orders.id',
  `used_at` datetime(3) NULL DEFAULT NULL COMMENT '核销时间',
  `client_request_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT 'P0-6: 客户端幂等键；同键重复发券只成功一次',
  `issued_by_user_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '发券人 user_id',
  `issued_at_store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '发券门店；NULL=不限门店使用',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `coupon_no`(`coupon_no` ASC) USING BTREE,
  UNIQUE INDEX `uq_cc_client_request`(`client_request_id` ASC) USING BTREE,
  INDEX `idx_cc_customer_status`(
    `customer_id` ASC,
    `status` ASC,
    `expires_at` ASC
  ) USING BTREE,
  INDEX `idx_cc_customer_template`(`customer_id` ASC, `template_id` ASC) USING BTREE,
  INDEX `idx_cc_issued_store`(`issued_at_store_id` ASC) USING BTREE,
  INDEX `idx_cc_issued_by`(`issued_by_user_id` ASC) USING BTREE,
  INDEX `fk_cc_template`(`template_id` ASC) USING BTREE,
  CONSTRAINT `fk_cc_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_cc_issued_store` FOREIGN KEY (`issued_at_store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_cc_issued_user` FOREIGN KEY (`issued_by_user_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_cc_template` FOREIGN KEY (`template_id`) REFERENCES `coupon_templates` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '用户优惠券/代金券实例表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of customer_coupons
-- ----------------------------
-- ----------------------------
-- Table structure for customer_identities
-- ----------------------------
DROP TABLE IF EXISTS `customer_identities`;
CREATE TABLE `customer_identities` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `provider` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'WECHAT_OPENID/WECHAT_UNIONID/ALIPAY_USER_ID/PHONE',
  `provider_subject` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '渠道稳定身份；日志中必须脱敏',
  `verified` tinyint NOT NULL DEFAULT 1 COMMENT '是否经渠道服务端验证',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_customer_identity_provider_subject`(`provider` ASC, `provider_subject` ASC) USING BTREE,
  INDEX `idx_customer_identity_customer`(`customer_id` ASC, `provider` ASC) USING BTREE,
  CONSTRAINT `fk_customer_identity_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '统一顾客稳定身份绑定' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of customer_identities
-- ----------------------------
-- ----------------------------
-- Table structure for customer_preferences
-- ----------------------------
DROP TABLE IF EXISTS `customer_preferences`;
CREATE TABLE `customer_preferences` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `category` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '规格组名称快照（如 辣度、份量）',
  `option_code` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '选项编码快照',
  `option_name` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '选项名称快照（展示用）',
  `preference_score` decimal(5, 4) NOT NULL DEFAULT 0.0000 COMMENT '偏好得分，越高越常点',
  `order_count` int NOT NULL DEFAULT 0 COMMENT '累计点选次数',
  `last_used_at` datetime(3) NULL DEFAULT NULL COMMENT '最近一次点选时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '统计更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_cust_pref`(
    `customer_id` ASC,
    `category` ASC,
    `option_code` ASC
  ) USING BTREE,
  INDEX `idx_pref_customer`(`customer_id` ASC, `category` ASC) USING BTREE,
  INDEX `idx_customer_prefs_score`(`customer_id` ASC, `preference_score` DESC) USING BTREE,
  CONSTRAINT `fk_pref_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '顾客口味偏好表（全门店汇总，不按店隔离）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of customer_preferences
-- ----------------------------
-- ----------------------------
-- Table structure for customer_taste_profiles
-- ----------------------------
DROP TABLE IF EXISTS `customer_taste_profiles`;
CREATE TABLE `customer_taste_profiles` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `dim_type` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '维度类型：CATEGORY/PRODUCT/TASTE_TAG',
  `target_key` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '目标键：分类/商品 id 字符串或标签字面量',
  `target_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '目标名称快照（展示用）',
  `score` decimal(6, 4) NOT NULL DEFAULT 0.0000 COMMENT '归一化偏好得分',
  `raw_weight` decimal(12, 4) NOT NULL DEFAULT 0.0000 COMMENT '衰减前原始权重',
  `event_count` int NOT NULL DEFAULT 0 COMMENT '累计行为事件数',
  `last_event_at` datetime(3) NULL DEFAULT NULL COMMENT '最近行为事件时间（增量水位）',
  `last_computed_at` datetime(3) NULL DEFAULT NULL COMMENT '最近重算时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_ctp_customer_dim_target`(
    `customer_id` ASC,
    `dim_type` ASC,
    `target_key` ASC
  ) USING BTREE,
  INDEX `idx_ctp_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_ctp_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '顾客多维口味画像表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of customer_taste_profiles
-- ----------------------------
-- ----------------------------
-- Table structure for customer_wechat_accounts
-- ----------------------------
DROP TABLE IF EXISTS `customer_wechat_accounts`;
CREATE TABLE `customer_wechat_accounts` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `openid` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '微信 openid，小程序内唯一',
  `unionid` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '微信 unionid（同主体多应用打通）',
  `profile_completed` tinyint NOT NULL DEFAULT 0 COMMENT '本渠道是否完善过昵称/头像资料（≠member_status）',
  `nickname` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '本渠道展示昵称（与支付宝互不覆盖）',
  `avatar_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '本渠道展示头像（与支付宝互不覆盖）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '首次绑定时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `openid`(`openid` ASC) USING BTREE,
  INDEX `idx_wechat_unionid`(`unionid` ASC) USING BTREE,
  INDEX `fk_cwa_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_cwa_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '微信账号绑定表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of customer_wechat_accounts
-- ----------------------------
-- ----------------------------
-- Table structure for customers
-- ----------------------------
DROP TABLE IF EXISTS `customers`;
CREATE TABLE `customers` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_no` varchar(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '顾客编号（业务可读单号）',
  `display_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '展示名（可与昵称相同）',
  `nickname` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '收银台/后台展示昵称（小程序微信/支付宝各存在对应账号表）',
  `avatar_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '收银台/后台展示头像（小程序按渠道账号分存）',
  `phone` varchar(11) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '手机号；绑定后可合并历史账号',
  `member_level_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '当前会员等级 → member_levels.id',
  `points` int NOT NULL DEFAULT 0 COMMENT '累计积分；实付1元=1分，达门槛自动升级',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '顾客状态：1=正常 0=禁用（兼容字段；以 account_status 为准）',
  `member_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'GUEST' COMMENT 'GUEST=游客（可点餐） MEMBER=已主动开通会员',
  `account_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/DISABLED/DELETED',
  `profile_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'EMPTY' COMMENT 'EMPTY/PARTIAL/COMPLETED 资料完善度（≠会员）',
  `phone_verified` tinyint NOT NULL DEFAULT 0 COMMENT '手机号是否已验证',
  `member_activated_at` datetime(3) NULL DEFAULT NULL COMMENT '首次主动开通会员时间（≠首次扫码）',
  `first_seen_at` datetime(3) NULL DEFAULT NULL COMMENT '首次扫码/识别时间（≠会员激活）',
  `first_seen_store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '首次扫码门店 → stores.id',
  `first_seen_table_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '首次扫码桌台 → tables.id',
  `token_version` int NOT NULL DEFAULT 0 COMMENT 'JWT 版本号；禁用/合并账号时递增，使旧令牌失效',
  `registered_store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '会员激活时门店 → stores.id；只记第一次',
  `registered_table_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '会员激活时桌台 → tables.id；只记第一次',
  `last_order_at` datetime(3) NULL DEFAULT NULL COMMENT '最近下单时间',
  `recommend_opt_in` int NOT NULL DEFAULT 1 COMMENT '个性化推荐开关：1=开启（默认） 0=关闭（退回非个性化热销榜，不采集行为）',
  `consent_version` varchar(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '个性化推荐同意的隐私政策版本（版本化留痕，PIPL 合规）',
  `recommend_consent_at` datetime(3) NULL DEFAULT NULL COMMENT '个性化推荐同意时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '首次建档时间（游客即可）',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `customer_no`(`customer_no` ASC) USING BTREE,
  INDEX `idx_customers_phone`(`phone` ASC) USING BTREE,
  INDEX `idx_customers_last_order`(`last_order_at` ASC) USING BTREE,
  INDEX `idx_customers_reg_store`(`registered_store_id` ASC) USING BTREE,
  INDEX `idx_customers_member_status`(`member_status` ASC) USING BTREE,
  INDEX `fk_customers_level`(`member_level_id` ASC) USING BTREE,
  INDEX `fk_customers_reg_table`(`registered_table_id` ASC) USING BTREE,
  CONSTRAINT `fk_customers_level` FOREIGN KEY (`member_level_id`) REFERENCES `member_levels` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_customers_reg_store` FOREIGN KEY (`registered_store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_customers_reg_table` FOREIGN KEY (`registered_table_id`) REFERENCES `tables` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '顾客主表（游客/会员统一身份）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of customers
-- ----------------------------
-- ----------------------------
-- Table structure for delivery_callback_logs
-- ----------------------------
DROP TABLE IF EXISTS `delivery_callback_logs`;
CREATE TABLE `delivery_callback_logs` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `platform` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'MEITUAN | ELEME',
  `event_type` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '回调事件类型',
  `event_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '平台事件 ID（幂等去重）',
  `platform_order_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '平台订单号',
  `order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '本系统订单 ID（解析后填入）',
  `payload` json NULL COMMENT '回调原文',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '接收时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_dcl_event`(`platform` ASC, `event_id` ASC) USING BTREE,
  INDEX `idx_dcl_platform_order`(
    `platform` ASC,
    `platform_order_id` ASC,
    `created_at` ASC
  ) USING BTREE,
  INDEX `idx_dcl_event`(`event_type` ASC, `created_at` ASC) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '外卖回调审计日志表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of delivery_callback_logs
-- ----------------------------
-- ----------------------------
-- Table structure for delivery_orders
-- ----------------------------
DROP TABLE IF EXISTS `delivery_orders`;
CREATE TABLE `delivery_orders` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '统一订单 → orders.id（一对一）',
  `platform` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'MEITUAN | ELEME',
  `platform_order_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '平台订单号',
  `platform_day_seq` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '平台当日流水号/取餐号',
  `delivery_status` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/ACCEPTED/READY/DELIVERING/DONE/CANCELLED',
  `recipient_name` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '收件人姓名',
  `recipient_phone` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '收件人电话（可能是隐私号）',
  `recipient_address` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '配送地址',
  `rider_name` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '骑手姓名',
  `rider_phone` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '骑手电话',
  `expected_delivery_at` datetime(3) NULL DEFAULT NULL COMMENT '预计送达时间',
  `platform_fee` decimal(10, 2) NULL DEFAULT 0.00 COMMENT '平台服务费',
  `packing_fee` decimal(10, 2) NULL DEFAULT 0.00 COMMENT '打包费（平台侧）',
  `delivery_fee` decimal(10, 2) NULL DEFAULT 0.00 COMMENT '配送费',
  `logistics_status` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '物流配送状态',
  `logistics_code` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '物流状态码',
  `refund_status` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT 'NONE' COMMENT 'NONE/PENDING/APPROVED/REJECTED',
  `urge_count` int NOT NULL DEFAULT 0 COMMENT '顾客催单次数',
  `completed_at` datetime(3) NULL DEFAULT NULL COMMENT '送达/完成时间',
  `raw_payload` json NULL COMMENT '平台原始订单 JSON',
  `last_sync_at` datetime(3) NULL DEFAULT NULL COMMENT '最近一次与平台同步时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `order_id`(`order_id` ASC) USING BTREE,
  UNIQUE INDEX `uk_platform_order`(`platform` ASC, `platform_order_id` ASC) USING BTREE,
  INDEX `idx_do_status`(`delivery_status` ASC, `created_at` ASC) USING BTREE,
  CONSTRAINT `fk_do_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '外卖订单扩展表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of delivery_orders
-- ----------------------------
-- ----------------------------
-- Table structure for delivery_product_maps
-- ----------------------------
DROP TABLE IF EXISTS `delivery_product_maps`;
CREATE TABLE `delivery_product_maps` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `platform` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'MEITUAN=美团 | ELEME=饿了么',
  `platform_sku_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '平台 SKU ID',
  `platform_spu_code` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '平台 SPU / app_food_code',
  `app_food_code` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '本系统对外商品编码',
  `option_signature` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '规格组合签名（区分同 SKU 不同加料）',
  `platform_sku_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '平台侧 SKU 名称',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '映射的本系统商品 → products.id',
  `option_map_json` json NULL COMMENT '平台规格项 → 本系统 options 的映射 JSON',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_platform_sku`(
    `store_id` ASC,
    `platform` ASC,
    `platform_sku_id` ASC
  ) USING BTREE,
  INDEX `idx_dpm_product`(`product_id` ASC) USING BTREE,
  CONSTRAINT `fk_dpm_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_dpm_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '外卖平台商品映射表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of delivery_product_maps
-- ----------------------------
-- ----------------------------
-- Table structure for delivery_refunds
-- ----------------------------
DROP TABLE IF EXISTS `delivery_refunds`;
CREATE TABLE `delivery_refunds` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `delivery_order_id` bigint UNSIGNED NOT NULL COMMENT '外卖扩展单 → delivery_orders.id',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '统一订单 → orders.id',
  `platform` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'MEITUAN | ELEME',
  `platform_refund_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '平台退款单号',
  `refund_type` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'FULL' COMMENT 'FULL=全额 | PARTIAL=部分',
  `amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '退款金额',
  `reason` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '退款原因',
  `status` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/APPROVED/REJECTED',
  `raw_payload` json NULL COMMENT '平台退款报文',
  `handled_at` datetime(3) NULL DEFAULT NULL COMMENT '处理完成时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_dr_order`(`order_id` ASC) USING BTREE,
  INDEX `fk_dr_do`(`delivery_order_id` ASC) USING BTREE,
  CONSTRAINT `fk_dr_do` FOREIGN KEY (`delivery_order_id`) REFERENCES `delivery_orders` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_dr_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '外卖退款单表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of delivery_refunds
-- ----------------------------
-- ----------------------------
-- Table structure for delivery_sync_alerts
-- ----------------------------
DROP TABLE IF EXISTS `delivery_sync_alerts`;
CREATE TABLE `delivery_sync_alerts` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联门店 stores.id（可空）',
  `platform` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT 'MEITUAN | ELEME',
  `alert_type` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'SYNC_FAIL' COMMENT 'REJECT_ORDER=库存拒单 | SYNC_FAIL=同步失败',
  `title` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '告警标题',
  `detail` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL COMMENT '告警详情/原因',
  `platform_order_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '关联平台订单号',
  `resolved` tinyint NOT NULL DEFAULT 0 COMMENT '0=未处理 1=已处理',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '产生时间',
  `resolved_at` datetime(3) NULL DEFAULT NULL COMMENT '处理时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_dsa_store_resolved`(`store_id` ASC, `resolved` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_dsa_platform_order`(`platform_order_id` ASC) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '外卖同步/拒单告警' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of delivery_sync_alerts
-- ----------------------------
-- ----------------------------
-- Table structure for dining_sessions
-- ----------------------------
DROP TABLE IF EXISTS `dining_sessions`;
CREATE TABLE `dining_sessions` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `table_id` bigint UNSIGNED NOT NULL COMMENT '桌台 → tables.id',
  `table_token` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '绑定时桌台二维码 token 快照',
  `status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/EXPIRED/COMPLETED/CANCELLED',
  `started_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '会话开始',
  `last_active_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '最近活跃',
  `expires_at` datetime(3) NOT NULL COMMENT '过期时间',
  `ended_at` datetime(3) NULL DEFAULT NULL COMMENT '结束时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_ds_customer_status`(
    `customer_id` ASC,
    `status` ASC,
    `expires_at` ASC
  ) USING BTREE,
  INDEX `idx_ds_store_table`(`store_id` ASC, `table_id` ASC, `status` ASC) USING BTREE,
  INDEX `fk_ds_table`(`table_id` ASC) USING BTREE,
  CONSTRAINT `fk_ds_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_ds_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_ds_table` FOREIGN KEY (`table_id`) REFERENCES `tables` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '就餐会话（扫码绑桌，非永久桌台归属）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of dining_sessions
-- ----------------------------
-- ----------------------------
-- Table structure for face_profiles
-- ----------------------------
DROP TABLE IF EXISTS `face_profiles`;
CREATE TABLE `face_profiles` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `feature_data` blob NOT NULL COMMENT '加密后的人脸特征向量',
  `feature_version` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'v1' COMMENT '特征提取算法版本',
  `consent_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'AUTHORIZED' COMMENT 'AUTHORIZED=已授权 | REVOKED=已撤销删除',
  `consent_at` datetime(3) NOT NULL COMMENT '授权采集时间',
  `revoked_at` datetime(3) NULL DEFAULT NULL COMMENT '撤销授权时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_face_customer`(`customer_id` ASC, `consent_status` ASC) USING BTREE,
  CONSTRAINT `fk_face_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '会员人脸特征档案表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of face_profiles
-- ----------------------------
-- ----------------------------
-- Table structure for financial_ledger
-- ----------------------------
DROP TABLE IF EXISTS `financial_ledger`;
CREATE TABLE `financial_ledger` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `entry_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'SALE=收款 | REFUND=退款 | ADJUSTMENT=调账',
  `channel` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '业务渠道：WECHAT/ALIPAY/POS/MEITUAN/ELEME 等',
  `provider` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '支付提供方：WECHAT/ALIPAY/CASH/MOCK',
  `amount` decimal(10, 2) NOT NULL COMMENT '金额（元；退款可为负或按 entry_type 区分）',
  `currency` varchar(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'CNY' COMMENT '币种',
  `order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联订单 → orders.id',
  `payment_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联支付单 → payments.id',
  `refund_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联退款单 → payment_refunds.id',
  `delivery_order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联外卖扩展单 → delivery_orders.id',
  `delivery_refund_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联外卖退款 → delivery_refunds.id',
  `remark` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '备注',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入账时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_fl_payment`(`payment_id` ASC) USING BTREE,
  UNIQUE INDEX `uk_fl_refund`(`refund_id` ASC) USING BTREE,
  UNIQUE INDEX `uk_fl_delivery_refund`(`delivery_refund_id` ASC) USING BTREE,
  UNIQUE INDEX `uq_fin_ledger_entry_payment`(`entry_type` ASC, `payment_id` ASC) USING BTREE,
  UNIQUE INDEX `uq_fin_ledger_entry_refund`(`entry_type` ASC, `refund_id` ASC) USING BTREE,
  UNIQUE INDEX `uq_fin_ledger_entry_delivery_order`(`entry_type` ASC, `delivery_order_id` ASC) USING BTREE,
  UNIQUE INDEX `uq_fin_ledger_entry_delivery_refund`(`entry_type` ASC, `delivery_refund_id` ASC) USING BTREE,
  INDEX `idx_fl_store_created`(`store_id` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_fl_store_ch_type_created`(
    `store_id` ASC,
    `channel` ASC,
    `entry_type` ASC,
    `created_at` ASC
  ) USING BTREE,
  INDEX `idx_fl_entry_type`(`entry_type` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_fl_order`(`order_id` ASC) USING BTREE,
  INDEX `idx_fl_payment`(`payment_id` ASC) USING BTREE,
  INDEX `idx_fl_refund`(`refund_id` ASC) USING BTREE,
  CONSTRAINT `fk_fl_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '财务分录账本' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of financial_ledger
-- ----------------------------
-- ----------------------------
-- Table structure for member_levels
-- ----------------------------
DROP TABLE IF EXISTS `member_levels`;
CREATE TABLE `member_levels` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `name` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '等级名称，如 普通会员、银卡会员',
  `min_points` int NOT NULL DEFAULT 0 COMMENT '升级所需累计积分下限',
  `discount_rate` decimal(5, 2) NOT NULL DEFAULT 1.00 COMMENT '折扣率：1.00=无折扣，0.90=九折',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '会员等级定义表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of member_levels
-- ----------------------------
INSERT INTO `member_levels`
VALUES (1, '普通会员', 0, 1.00, '2026-09-20 15:04:33.046');
INSERT INTO `member_levels`
VALUES (2, '银卡会员', 200, 0.98, '2026-09-20 15:04:33.050');
INSERT INTO `member_levels`
VALUES (3, '金卡会员', 500, 0.95, '2026-09-20 15:04:33.052');
INSERT INTO `member_levels`
VALUES (4, '钻石会员', 1000, 0.90, '2026-09-20 15:04:33.052');
-- ----------------------------
-- Table structure for operation_logs
-- ----------------------------
DROP TABLE IF EXISTS `operation_logs`;
CREATE TABLE `operation_logs` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `user_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '操作人 → users.id；系统自动任务可为 NULL',
  `action` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '动作编码，如 order.cancel、product.update',
  `module` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '业务模块',
  `target_type` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '目标实体类型，如 Order、Product',
  `target_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '目标实体主键',
  `ip` varchar(45) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '客户端 IP（支持 IPv6）',
  `user_agent` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '浏览器/客户端 UA',
  `request_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '请求链路 ID，便于与日志系统关联',
  `detail` json NULL COMMENT '变更详情、请求参数摘要等 JSON',
  `category` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT 'STAFF' COMMENT '审计分类：STAFF/STORE_ADMIN/CASHIER/KITCHEN/SYSTEM/CUSTOMER/DANGER/INTRUSION',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '操作关联门店；NULL=全局/未绑定（如登录失败、跨店系统操作）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '记录时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_op_logs_user`(`user_id` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_op_logs_module`(`module` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_op_logs_created`(`created_at` ASC) USING BTREE,
  INDEX `idx_op_logs_category`(`category` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_op_logs_store`(`store_id` ASC, `created_at` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '后台操作审计日志' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of operation_logs
-- ----------------------------
-- ----------------------------
-- Table structure for option_groups
-- ----------------------------
DROP TABLE IF EXISTS `option_groups`;
CREATE TABLE `option_groups` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT 'NULL=总部共享；非空=仅该门店',
  `name` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '规格组显示名',
  `code` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '规格组编码：SH01-Liang 或 SH01-Liang',
  `selection_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'SINGLE' COMMENT 'SINGLE=单选 | MULTIPLE=多选',
  `is_required` tinyint NOT NULL DEFAULT 1 COMMENT '是否必选：1=必选 0=可选',
  `sort_order` int NOT NULL DEFAULT 0 COMMENT '展示排序',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  `store_scope` bigint UNSIGNED GENERATED ALWAYS AS (ifnull(`store_id`, 0)) STORED COMMENT '唯一约束用：NULL store→0' NULL,
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_og_store_code`(`store_scope` ASC, `code` ASC) USING BTREE,
  INDEX `fk_og_store`(`store_id` ASC) USING BTREE,
  CONSTRAINT `fk_og_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '商品规格组表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of option_groups
-- ----------------------------
INSERT INTO `option_groups`
VALUES (
    1,
    1,
    '姜末',
    'SH01-JiangMo',
    'SINGLE',
    0,
    7,
    1,
    DEFAULT,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `option_groups`
VALUES (
    2,
    1,
    '面型',
    'SH01-MianXing',
    'SINGLE',
    0,
    8,
    1,
    DEFAULT,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `option_groups`
VALUES (
    3,
    1,
    '口感',
    'SH01-KouGan',
    'SINGLE',
    0,
    9,
    1,
    DEFAULT,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `option_groups`
VALUES (
    4,
    1,
    '汤汁',
    'SH01-TangZhi',
    'SINGLE',
    0,
    10,
    1,
    DEFAULT,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `option_groups`
VALUES (
    5,
    1,
    '加臊子',
    'SH01-JiaSaoZi',
    'MULTIPLE',
    0,
    11,
    1,
    DEFAULT,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `option_groups`
VALUES (
    6,
    1,
    '加蛋加菜',
    'SH01-JiaDanJiaCai',
    'MULTIPLE',
    0,
    12,
    1,
    DEFAULT,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `option_groups`
VALUES (
    7,
    1,
    '汤底',
    'SH01-TangDi',
    'SINGLE',
    0,
    13,
    1,
    DEFAULT,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `option_groups`
VALUES (
    8,
    1,
    '皮薄厚',
    'SH01-PiBaoHou',
    'SINGLE',
    0,
    14,
    1,
    DEFAULT,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `option_groups`
VALUES (
    9,
    1,
    '甜度',
    'SH01-TianDu',
    'SINGLE',
    0,
    15,
    1,
    DEFAULT,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `option_groups`
VALUES (
    10,
    1,
    '荷包蛋熟度',
    'SH01-ShuDu',
    'SINGLE',
    0,
    16,
    1,
    DEFAULT,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `option_groups`
VALUES (
    11,
    1,
    '汤圆数量',
    'SH01-TangYuanShuLiang',
    'SINGLE',
    1,
    17,
    1,
    DEFAULT,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `option_groups`
VALUES (
    12,
    1,
    '包子个数',
    'SH01-BaoZiGeShu',
    'SINGLE',
    1,
    18,
    1,
    DEFAULT,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `option_groups`
VALUES (
    13,
    1,
    '是否加热',
    'SH01-ShiFouJiaRe',
    'SINGLE',
    1,
    19,
    1,
    DEFAULT,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- ----------------------------
-- Table structure for options
-- ----------------------------
DROP TABLE IF EXISTS `options`;
CREATE TABLE `options` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `group_id` bigint UNSIGNED NOT NULL COMMENT '规格组 → option_groups.id',
  `name` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '选项显示名，如 大份、微辣',
  `code` varchar(80) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '选项编码：SH01-Liang-XiaoWan 或 SH01-Liang-XiaoWan',
  `extra_price` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '相对基础价的加价',
  `sort_order` int NOT NULL DEFAULT 0 COMMENT '组内排序',
  `is_default` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否默认选项（同组单选仅一个；菜单+一键加购）',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_group_code`(`group_id` ASC, `code` ASC) USING BTREE,
  CONSTRAINT `fk_options_group` FOREIGN KEY (`group_id`) REFERENCES `option_groups` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `chk_options_extra_price` CHECK (`extra_price` >= 0)
) ENGINE = InnoDB AUTO_INCREMENT = 45 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '规格选项表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of options
-- ----------------------------
INSERT INTO `options`
VALUES (
    1,
    1,
    '不加姜末',
    'SH01-JiangMo-BuJiaJiangMo',
    0.00,
    0,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    2,
    1,
    '微量姜末',
    'SH01-JiangMo-WeiLiangJiangMo',
    0.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    3,
    1,
    '正常姜末',
    'SH01-JiangMo-ZhengChangJiangMo',
    0.00,
    2,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    4,
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
INSERT INTO `options`
VALUES (
    5,
    2,
    '细面',
    'SH01-MianXing-XiMian',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    6,
    2,
    '宽面',
    'SH01-MianXing-KuanMian',
    0.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    7,
    2,
    '韭菜叶',
    'SH01-MianXing-JiuCaiYe',
    0.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    8,
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
INSERT INTO `options`
VALUES (
    9,
    3,
    '软一点',
    'SH01-KouGan-RuanYiDian',
    0.00,
    0,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    10,
    3,
    '标准',
    'SH01-KouGan-BiaoZhun',
    0.00,
    1,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    11,
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
INSERT INTO `options`
VALUES (
    12,
    4,
    '正常汤',
    'SH01-TangZhi-ZhengChangTang',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    13,
    4,
    '多汤',
    'SH01-TangZhi-DuoTang',
    0.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    14,
    4,
    '少汤',
    'SH01-TangZhi-ShaoTang',
    0.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    15,
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
INSERT INTO `options`
VALUES (
    16,
    5,
    '加杂酱',
    'SH01-JiaSaoZi-ZaJiang',
    3.00,
    0,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    17,
    5,
    '加鸡杂',
    'SH01-JiaSaoZi-JiZa',
    4.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    18,
    5,
    '加蹄花',
    'SH01-JiaSaoZi-TiHua',
    6.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    19,
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
INSERT INTO `options`
VALUES (
    20,
    6,
    '加煎蛋',
    'SH01-JiaDanJiaCai-JianDan',
    2.00,
    0,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    21,
    6,
    '加荷包蛋',
    'SH01-JiaDanJiaCai-HeBaoDan',
    2.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    22,
    6,
    '加豌豆',
    'SH01-JiaDanJiaCai-WanDou',
    1.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    23,
    6,
    '加青菜',
    'SH01-JiaDanJiaCai-QingCai',
    1.00,
    3,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    24,
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
INSERT INTO `options`
VALUES (
    25,
    7,
    '清汤',
    'SH01-TangDi-QingTang',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    26,
    7,
    '红油',
    'SH01-TangDi-HongYou',
    0.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    27,
    7,
    '酸辣',
    'SH01-TangDi-SuanLa',
    0.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    28,
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
INSERT INTO `options`
VALUES (
    29,
    8,
    '标准',
    'SH01-PiBaoHou-BiaoZhun',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    30,
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
INSERT INTO `options`
VALUES (
    31,
    9,
    '正常糖',
    'SH01-TianDu-ZhengChangTang',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    32,
    9,
    '少糖',
    'SH01-TianDu-ShaoTang',
    0.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    33,
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
INSERT INTO `options`
VALUES (
    34,
    10,
    '溏心',
    'SH01-ShuDu-TangXin',
    0.00,
    0,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    35,
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
INSERT INTO `options`
VALUES (
    36,
    11,
    '4个',
    'SH01-TangYuanShuLiang-4Ge',
    0.00,
    0,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    37,
    11,
    '6个',
    'SH01-TangYuanShuLiang-6Ge',
    0.00,
    1,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    38,
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
INSERT INTO `options`
VALUES (
    39,
    12,
    '1个',
    'SH01-BaoZiGeShu-1Ge',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    40,
    12,
    '2个',
    'SH01-BaoZiGeShu-2Ge',
    2.00,
    1,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    41,
    12,
    '半笼(3个)',
    'SH01-BaoZiGeShu-BanLong',
    4.00,
    2,
    0,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    42,
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
INSERT INTO `options`
VALUES (
    43,
    13,
    '现蒸',
    'SH01-ShiFouJiaRe-XianZheng',
    0.00,
    0,
    1,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `options`
VALUES (
    44,
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
-- ----------------------------
-- Table structure for order_item_options
-- ----------------------------
DROP TABLE IF EXISTS `order_item_options`;
CREATE TABLE `order_item_options` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_item_id` bigint UNSIGNED NOT NULL COMMENT '订单明细 → order_items.id',
  `group_name_snapshot` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '规格组名称快照',
  `option_name_snapshot` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '选项名称快照',
  `option_code` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '选项编码快照（口味统计用）',
  `extra_price_snapshot` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '下单时该选项加价快照',
  `quantity` int NOT NULL DEFAULT 1 COMMENT '该选项数量（多选场景）',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_oio_item`(`order_item_id` ASC) USING BTREE,
  CONSTRAINT `fk_oio_item` FOREIGN KEY (`order_item_id`) REFERENCES `order_items` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '订单明细规格快照表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of order_item_options
-- ----------------------------
-- ----------------------------
-- Table structure for order_items
-- ----------------------------
DROP TABLE IF EXISTS `order_items`;
CREATE TABLE `order_items` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '商品 → products.id（快照时有效 ID）',
  `product_name_snapshot` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '下单时商品名称快照',
  `unit_price_snapshot` decimal(10, 2) NOT NULL COMMENT '下单时单价快照（含基础价，不含规格加价）',
  `quantity` int NOT NULL DEFAULT 1 COMMENT '购买数量',
  `subtotal` decimal(10, 2) NOT NULL COMMENT '行小计 = (单价+规格加价)×数量',
  `remark` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '单品备注（如 少葱）',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_oi_order`(`order_id` ASC) USING BTREE,
  INDEX `fk_oi_product`(`product_id` ASC) USING BTREE,
  CONSTRAINT `fk_oi_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_oi_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '订单商品明细表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of order_items
-- ----------------------------
-- ----------------------------
-- Table structure for order_status_history
-- ----------------------------
DROP TABLE IF EXISTS `order_status_history`;
CREATE TABLE `order_status_history` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  `from_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '变更前状态',
  `to_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '变更后状态',
  `source` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '变更来源：STAFF=员工 | PAYMENT=支付 | SYSTEM=系统 | DELIVERY=外卖',
  `operator_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '操作员工 → users.id（如有）',
  `remark` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '变更说明',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '变更时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_osh_order`(`order_id` ASC, `created_at` ASC) USING BTREE,
  CONSTRAINT `fk_osh_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '订单状态变更历史表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of order_status_history
-- ----------------------------
-- ----------------------------
-- Table structure for orders
-- ----------------------------
DROP TABLE IF EXISTS `orders`;
CREATE TABLE `orders` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_no` varchar(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '业务订单号（含门店/桌位/时间等可读信息）',
  `customer_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '下单顾客 → customers.id；匿名单可为空',
  `table_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '堂食桌台 → tables.id；外卖/打包可空',
  `dining_session_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '就餐会话 → dining_sessions.id',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PENDING_PAYMENT' COMMENT '订单履约状态，见文件头枚举',
  `dining_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'DINE_IN' COMMENT 'DINE_IN=堂食 | TAKEAWAY=打包外带',
  `subtotal` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '商品折前小计（含规格加价）',
  `discount_amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '会员等级折扣减免金额',
  `customer_coupon_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '使用的优惠券 → customer_coupons.id',
  `coupon_discount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '优惠券减免金额（非代金券）',
  `customer_voucher_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '使用的代金券 → customer_coupons.id',
  `voucher_discount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '代金券抵扣金额',
  `packing_fee` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '打包/餐盒费合计',
  `total_amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '应付总额（见文件头计算公式）',
  `payment_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'UNPAID' COMMENT 'UNPAID/PARTIAL_PAID/PAID/PARTIAL_REFUND/REFUNDING/REFUNDED',
  `payment_method` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '支付方式摘要：WECHAT/ALIPAY/CASH/MIXED',
  `channel` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '下单渠道：WECHAT/ALIPAY/MEITUAN/ELEME/H5',
  `remark` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '顾客备注',
  `idempotency_key` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '下单幂等键（按渠道/门店/操作员等作用域拼接，防重复提交；允许多 NULL）',
  `operator_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '下单/收银操作员 → users.id（POS 渠道必填）',
  `ordered_at` datetime(3) NULL DEFAULT NULL COMMENT '下单时间（业务时间）',
  `paid_at` datetime(3) NULL DEFAULT NULL COMMENT '支付成功时间',
  `completed_at` datetime(3) NULL DEFAULT NULL COMMENT '订单完成时间',
  `cancelled_at` datetime(3) NULL DEFAULT NULL COMMENT '取消时间',
  `kitchen_claimed_by` bigint UNSIGNED NULL DEFAULT NULL COMMENT '厨房认领人 → users.id；点「开始制作」写入',
  `kitchen_claimed_at` datetime(3) NULL DEFAULT NULL COMMENT '厨房认领时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '记录创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '记录更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `order_no`(`order_no` ASC) USING BTREE,
  UNIQUE INDEX `uk_idempotency`(`idempotency_key` ASC) USING BTREE,
  INDEX `idx_orders_customer`(`customer_id` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_orders_table`(`table_id` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_orders_dining_session`(`dining_session_id` ASC) USING BTREE,
  INDEX `idx_orders_status`(`status` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_orders_payment`(`payment_status` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_orders_status_paid`(`status` ASC, `paid_at` ASC) USING BTREE,
  INDEX `idx_orders_store_created`(`store_id` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_orders_store_paid`(`store_id` ASC, `paid_at` ASC) USING BTREE,
  INDEX `idx_orders_store_pay_paid`(
    `store_id` ASC,
    `payment_status` ASC,
    `paid_at` ASC
  ) USING BTREE,
  INDEX `idx_orders_kitchen_claim`(`kitchen_claimed_by` ASC, `status` ASC) USING BTREE,
  INDEX `idx_orders_operator`(`operator_id` ASC) USING BTREE,
  CONSTRAINT `fk_orders_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_orders_kitchen_claim` FOREIGN KEY (`kitchen_claimed_by`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_orders_operator` FOREIGN KEY (`operator_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_orders_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_orders_table` FOREIGN KEY (`table_id`) REFERENCES `tables` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '订单主表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of orders
-- ----------------------------
-- ----------------------------
-- Table structure for outbox_events
-- ----------------------------
DROP TABLE IF EXISTS `outbox_events`;
CREATE TABLE `outbox_events` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `aggregate_type` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '聚合类型：refund / paid_side_effect / ...',
  `aggregate_id` bigint UNSIGNED NOT NULL COMMENT '聚合 ID（refund_id / order_id / ...）',
  `event_kind` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '事件类型：refund_finalize / points_award / coupon_mark_used / pref_update / points_deduct_refund / pref_recompute',
  `payload` mediumtext CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL COMMENT 'JSON 负载（参数快照）',
  `status` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING / PROCESSING / DONE / FAILED',
  `attempt_count` int NOT NULL DEFAULT 0 COMMENT '消费尝试次数',
  `max_attempts` int NOT NULL DEFAULT 8 COMMENT '最大重试次数',
  `next_retry_at` datetime(3) NULL DEFAULT NULL COMMENT '下次允许重试时间',
  `locked_at` datetime(3) NULL DEFAULT NULL COMMENT '消费者加锁时间',
  `locked_by` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '锁持有者标识（hostname:pid）',
  `last_error` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '最近一次错误摘要',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  `processed_at` datetime(3) NULL DEFAULT NULL COMMENT '处理完成时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_outbox_agg`(
    `aggregate_type` ASC,
    `aggregate_id` ASC,
    `event_kind` ASC
  ) USING BTREE,
  INDEX `idx_oe_status_retry`(`status` ASC, `next_retry_at` ASC) USING BTREE,
  INDEX `idx_oe_status_locked`(`status` ASC, `locked_at` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = 'DB 事务型 outbox 事件' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of outbox_events
-- ----------------------------
-- ----------------------------
-- Table structure for payment_callback_receipts
-- ----------------------------
DROP TABLE IF EXISTS `payment_callback_receipts`;
CREATE TABLE `payment_callback_receipts` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `provider` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '支付渠道',
  `receipt_key` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '幂等键（如 notify_id + 交易号）',
  `payment_no` varchar(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '关联支付单号',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '首次收到时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_receipt`(`provider` ASC, `receipt_key` ASC) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '支付回调幂等回执表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of payment_callback_receipts
-- ----------------------------
-- ----------------------------
-- Table structure for payment_refunds
-- ----------------------------
DROP TABLE IF EXISTS `payment_refunds`;
CREATE TABLE `payment_refunds` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `refund_no` varchar(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '退款单号',
  `payment_id` bigint UNSIGNED NOT NULL COMMENT '原支付单 → payments.id',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '关联订单 → orders.id',
  `amount` decimal(10, 2) NOT NULL COMMENT '退款金额',
  `reason` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '退款原因',
  `status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PROCESSING/SUCCESS/FAILED/CLOSED',
  `provider_refund_no` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '第三方退款单号',
  `callback_raw` json NULL COMMENT '退款回调/查询原文',
  `fail_reason` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '失败或关闭原因',
  `succeeded_at` datetime(3) NULL DEFAULT NULL COMMENT '退款成功确认时间',
  `operator_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '发起退款员工 → users.id',
  `retry_count` int NOT NULL DEFAULT 0 COMMENT '主动查询/重试次数',
  `next_retry_at` datetime(3) NULL DEFAULT NULL COMMENT '下次允许重试时间',
  `last_error` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '最近一次失败摘要',
  `idempotency_key` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '退款幂等键（防重复发起）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `refund_no`(`refund_no` ASC) USING BTREE,
  UNIQUE INDEX `uk_refund_idempotency`(`idempotency_key` ASC) USING BTREE,
  INDEX `idx_refunds_status`(`status` ASC) USING BTREE,
  INDEX `idx_refunds_payment`(`payment_id` ASC) USING BTREE,
  INDEX `idx_refunds_operator`(`operator_id` ASC) USING BTREE,
  INDEX `idx_refunds_succeeded`(`status` ASC, `succeeded_at` ASC) USING BTREE,
  INDEX `idx_refunds_retry`(`status` ASC, `next_retry_at` ASC) USING BTREE,
  INDEX `fk_refund_order`(`order_id` ASC) USING BTREE,
  CONSTRAINT `fk_refund_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_refund_payment` FOREIGN KEY (`payment_id`) REFERENCES `payments` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '支付退款单表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of payment_refunds
-- ----------------------------
-- ----------------------------
-- Table structure for payment_status_history
-- ----------------------------
DROP TABLE IF EXISTS `payment_status_history`;
CREATE TABLE `payment_status_history` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `payment_id` bigint UNSIGNED NOT NULL COMMENT '支付单 → payments.id',
  `from_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '变更前状态',
  `to_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '变更后状态',
  `source` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'CREATE/CALLBACK/REFUND/QUERY',
  `remark` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '备注',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '变更时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_psh_payment`(`payment_id` ASC, `created_at` ASC) USING BTREE,
  CONSTRAINT `fk_psh_payment` FOREIGN KEY (`payment_id`) REFERENCES `payments` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '支付状态变更历史表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of payment_status_history
-- ----------------------------
-- ----------------------------
-- Table structure for payments
-- ----------------------------
DROP TABLE IF EXISTS `payments`;
CREATE TABLE `payments` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `payment_no` varchar(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '支付单号',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '关联订单 → orders.id',
  `customer_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '付款顾客 → customers.id',
  `operator_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '收银操作员 → users.id',
  `provider` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '支付渠道：WECHAT | ALIPAY | CASH | MOCK（开发模拟）',
  `channel` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '细分渠道，如 miniprogram、native、h5',
  `amount` decimal(10, 2) NOT NULL COMMENT '支付金额（元）',
  `currency` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'CNY' COMMENT '币种',
  `status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PAID/PARTIAL_REFUND/REFUNDED/CLOSED/FAILED',
  `provider_trade_no` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '第三方交易号',
  `provider_order_no` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '第三方商户订单号',
  `callback_raw` json NULL COMMENT '支付回调原文（审计）',
  `paid_at` datetime(3) NULL DEFAULT NULL COMMENT '支付成功时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `payment_no`(`payment_no` ASC) USING BTREE,
  UNIQUE INDEX `uk_provider_trade`(`provider` ASC, `provider_trade_no` ASC) USING BTREE,
  INDEX `idx_payments_order`(`order_id` ASC) USING BTREE,
  INDEX `idx_payments_no`(`payment_no` ASC) USING BTREE,
  INDEX `idx_payments_order_status`(`order_id` ASC, `status` ASC) USING BTREE,
  INDEX `idx_payments_created`(`created_at` ASC) USING BTREE,
  INDEX `idx_payments_paid_at`(`paid_at` ASC) USING BTREE,
  INDEX `idx_payments_status_paid`(`status` ASC, `paid_at` ASC) USING BTREE,
  INDEX `idx_payments_operator`(`operator_id` ASC, `paid_at` ASC) USING BTREE,
  INDEX `fk_payments_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_payments_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_payments_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '支付单表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of payments
-- ----------------------------
-- ----------------------------
-- Table structure for permissions
-- ----------------------------
DROP TABLE IF EXISTS `permissions`;
CREATE TABLE `permissions` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `code` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '权限码，如 product:read、order:update、welcome:manage',
  `name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '权限名称（中文展示）',
  `module` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '所属模块：product/order/payment/customer 等',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `code`(`code` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 21 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '权限点表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of permissions
-- ----------------------------
INSERT INTO `permissions`
VALUES (
    1,
    'product:read',
    '查看商品',
    'product',
    '2026-09-20 15:04:32.984'
  );
INSERT INTO `permissions`
VALUES (
    2,
    'product:create',
    '创建商品',
    'product',
    '2026-09-20 15:04:32.988'
  );
INSERT INTO `permissions`
VALUES (
    3,
    'product:update',
    '更新商品',
    'product',
    '2026-09-20 15:04:32.990'
  );
INSERT INTO `permissions`
VALUES (
    4,
    'product:delete',
    '删除商品',
    'product',
    '2026-09-20 15:04:32.991'
  );
INSERT INTO `permissions`
VALUES (
    5,
    'order:read',
    '查看订单',
    'order',
    '2026-09-20 15:04:32.994'
  );
INSERT INTO `permissions`
VALUES (
    6,
    'order:update',
    '更新订单',
    'order',
    '2026-09-20 15:04:32.996'
  );
INSERT INTO `permissions`
VALUES (
    7,
    'customer:read',
    '查看顾客',
    'customer',
    '2026-09-20 15:04:32.998'
  );
INSERT INTO `permissions`
VALUES (
    8,
    'customer:manage',
    '管理会员等级',
    'customer',
    '2026-09-20 15:04:33.000'
  );
INSERT INTO `permissions`
VALUES (
    9,
    'camera:read',
    '查看摄像头',
    'camera',
    '2026-09-20 15:04:33.002'
  );
INSERT INTO `permissions`
VALUES (
    10,
    'camera:manage',
    '管理摄像头',
    'camera',
    '2026-09-20 15:04:33.004'
  );
INSERT INTO `permissions`
VALUES (
    11,
    'welcome:manage',
    '管理迎宾',
    'welcome',
    '2026-09-20 15:04:33.006'
  );
INSERT INTO `permissions`
VALUES (
    12,
    'payment:read',
    '查看支付',
    'payment',
    '2026-09-20 15:04:33.009'
  );
INSERT INTO `permissions`
VALUES (
    13,
    'payment:refund',
    '支付与外卖退款',
    'payment',
    '2026-09-20 15:04:33.011'
  );
INSERT INTO `permissions`
VALUES (
    14,
    'table:manage',
    '管理桌台',
    'table',
    '2026-09-20 15:04:33.014'
  );
INSERT INTO `permissions`
VALUES (
    15,
    'user:read',
    '查看员工',
    'user',
    '2026-09-20 15:04:33.016'
  );
INSERT INTO `permissions`
VALUES (
    16,
    'user:manage',
    '管理员工',
    'user',
    '2026-09-20 15:04:33.018'
  );
INSERT INTO `permissions`
VALUES (
    17,
    'system:manage',
    '系统管理',
    'system',
    '2026-09-20 15:04:33.020'
  );
INSERT INTO `permissions`
VALUES (
    18,
    'coupon:read',
    '查看优惠券',
    'coupon',
    '2026-09-20 15:04:33.022'
  );
INSERT INTO `permissions`
VALUES (
    19,
    'coupon:manage',
    '管理优惠券',
    'coupon',
    '2026-09-20 15:04:33.025'
  );
INSERT INTO `permissions`
VALUES (
    20,
    'audit:read',
    '查看操作日志',
    'audit',
    '2026-09-20 15:04:33.027'
  );
-- ----------------------------
-- Table structure for platform_category_maps
-- ----------------------------
DROP TABLE IF EXISTS `platform_category_maps`;
CREATE TABLE `platform_category_maps` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '门店 → stores.id；NULL=总部默认映射',
  `platform` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'MEITUAN | ELEME',
  `local_category_id` bigint UNSIGNED NOT NULL COMMENT '本地分类 → product_categories.id',
  `platform_category_code` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '平台侧分类编码',
  `platform_category_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '平台侧分类名称',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_pcm_store_platform_cat`(
    `store_id` ASC,
    `platform` ASC,
    `local_category_id` ASC
  ) USING BTREE,
  INDEX `idx_pcm_platform`(`platform` ASC, `status` ASC) USING BTREE,
  INDEX `fk_pcm_cat`(`local_category_id` ASC) USING BTREE,
  CONSTRAINT `fk_pcm_cat` FOREIGN KEY (`local_category_id`) REFERENCES `product_categories` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_pcm_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '本地分类 ↔ 外卖平台分类映射' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of platform_category_maps
-- ----------------------------
-- ----------------------------
-- Table structure for platform_store_bindings
-- ----------------------------
DROP TABLE IF EXISTS `platform_store_bindings`;
CREATE TABLE `platform_store_bindings` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '本系统门店 → stores.id',
  `platform` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'MEITUAN | ELEME',
  `platform_shop_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '平台门店 ID',
  `platform_shop_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '平台门店名称',
  `access_token` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL COMMENT 'OAuth access_token',
  `refresh_token` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL COMMENT 'OAuth refresh_token',
  `token_expires_at` datetime(3) NULL DEFAULT NULL COMMENT 'token 过期时间',
  `extra_config` json NULL COMMENT '扩展配置（app_key、回调地址等）',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=已绑定 0=已解绑',
  `auto_sync_open` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否按营业时间自动开/关店（默认关闭，逐店开启）',
  `platform_open_status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '最近同步到平台的营业状态 OPEN/REST/OFFLINE',
  `last_sync_at` datetime(3) NULL DEFAULT NULL COMMENT '最近一次与平台同步时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_store_platform_shop`(
    `store_id` ASC,
    `platform` ASC,
    `platform_shop_id` ASC
  ) USING BTREE,
  CONSTRAINT `fk_psb_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '外卖平台门店授权绑定表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of platform_store_bindings
-- ----------------------------
-- ----------------------------
-- Table structure for point_ledger
-- ----------------------------
DROP TABLE IF EXISTS `point_ledger`;
CREATE TABLE `point_ledger` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联订单 → orders.id',
  `refund_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联退款单 → payment_refunds.id',
  `delta` int NOT NULL COMMENT '积分变动（可正可负）',
  `balance_after` int NOT NULL COMMENT '变动后余额',
  `entry_type` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '变动类型：EARN/REFUND_DEDUCT/ADJUST 等',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '记录时间',
  `earn_order_id` bigint UNSIGNED GENERATED ALWAYS AS (
    if((`entry_type` = _utf8mb4 'EARN'), `order_id`, NULL)
  ) STORED COMMENT 'EARN 幂等键：每单至多一条发放' NULL,
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_pl_refund`(`refund_id` ASC) USING BTREE,
  UNIQUE INDEX `uk_pl_earn_order`(`earn_order_id` ASC) USING BTREE,
  INDEX `idx_pl_customer_created`(`customer_id` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_pl_order`(`order_id` ASC) USING BTREE,
  INDEX `idx_pl_refund`(`refund_id` ASC) USING BTREE,
  CONSTRAINT `fk_pl_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '会员积分流水' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of point_ledger
-- ----------------------------
-- ----------------------------
-- Table structure for pos_refund_requests
-- ----------------------------
DROP TABLE IF EXISTS `pos_refund_requests`;
CREATE TABLE `pos_refund_requests` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  `payment_id` bigint UNSIGNED NOT NULL COMMENT '主支付单（组合支付时为分摊首笔）→ payments.id',
  `amount` decimal(10, 2) NOT NULL COMMENT '申请退款金额（元）',
  `executed_amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '已成功执行退款合计（组合分摊）',
  `reason` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '申请原因',
  `status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/REJECTED/CANCELLED/SUCCESS/FAILED',
  `requested_by` bigint UNSIGNED NOT NULL COMMENT '申请人（收银员）→ users.id',
  `reviewed_by` bigint UNSIGNED NULL DEFAULT NULL COMMENT '审批人 → users.id',
  `reviewed_at` datetime(3) NULL DEFAULT NULL COMMENT '审批时间',
  `review_remark` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '审批备注（拒绝原因等）',
  `refund_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '执行后的退款单 → payment_refunds.id',
  `fail_reason` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '执行失败原因',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_pos_refund_store_status`(`store_id` ASC, `status` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_pos_refund_order`(`order_id` ASC) USING BTREE,
  INDEX `idx_pos_refund_requester`(`requested_by` ASC, `created_at` ASC) USING BTREE,
  INDEX `fk_pos_refund_payment`(`payment_id` ASC) USING BTREE,
  CONSTRAINT `fk_pos_refund_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_pos_refund_payment` FOREIGN KEY (`payment_id`) REFERENCES `payments` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_pos_refund_requester` FOREIGN KEY (`requested_by`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_pos_refund_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '收银退款申请' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of pos_refund_requests
-- ----------------------------
-- ----------------------------
-- Table structure for print_jobs
-- ----------------------------
DROP TABLE IF EXISTS `print_jobs`;
CREATE TABLE `print_jobs` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  `ticket_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'KITCHEN' COMMENT 'KITCHEN/URGE/CANCEL',
  `content_hash` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '内容 SHA-256',
  `content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL COMMENT '小票内容（已清控制字符，可截断）',
  `status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PRINTING/SUCCESS/FAILED/RETRYING',
  `attempt_count` int NOT NULL DEFAULT 0 COMMENT '已尝试次数（含当前）',
  `next_retry_at` datetime(3) NULL DEFAULT NULL COMMENT '下次允许重试时间（指数退避）',
  `last_error` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '最近一次失败摘要',
  `locked_at` datetime(3) NULL DEFAULT NULL COMMENT '消费者加锁时间',
  `locked_by` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '锁持有者标识（实例/worker）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  `succeeded_at` datetime(3) NULL DEFAULT NULL COMMENT '打印成功时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_pj_order`(`order_id` ASC, `ticket_type` ASC) USING BTREE,
  INDEX `idx_pj_store_status`(`store_id` ASC, `status` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_pj_retry`(`status` ASC, `next_retry_at` ASC) USING BTREE,
  INDEX `idx_pj_locked`(`locked_at` ASC, `locked_by` ASC) USING BTREE,
  CONSTRAINT `fk_pj_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_pj_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '厨房小票打印任务表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of print_jobs
-- ----------------------------
-- ----------------------------
-- Table structure for product_categories
-- ----------------------------
DROP TABLE IF EXISTS `product_categories`;
CREATE TABLE `product_categories` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '所属门店 → stores.id',
  `category_no` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '分类编号：{store_no}-拼音逻辑，如 SH01-Mian',
  `name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '分类名称（如 招牌面、小吃）',
  `description` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '分类描述',
  `sort_order` int NOT NULL DEFAULT 0 COMMENT '排序权重，越小越靠前',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=展示 0=隐藏',
  `require_category_ids` json NULL COMMENT '须先点过的分类 id 列表（OR：满足任一即可）；NULL/空=无限制。例：加料依赖面',
  `recommend_config` json NULL COMMENT '推荐差异化配置：权重/复购窗口覆盖等（智能推荐 0018）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_cat_store_no`(`store_id` ASC, `category_no` ASC) USING BTREE,
  INDEX `idx_cat_store`(`store_id` ASC, `sort_order` ASC, `status` ASC) USING BTREE,
  CONSTRAINT `fk_cat_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 6 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '商品分类表（按门店）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of product_categories
-- ----------------------------
INSERT INTO `product_categories`
VALUES (
    1,
    1,
    'SH01-ZhaoPaiMian',
    '招牌面·汤面',
    '射洪招牌汤面：鸡杂/杂酱/豌杂/笋子牛肉/蹄花/肠旺面',
    6,
    1,
    NULL,
    NULL,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `product_categories`
VALUES (
    2,
    1,
    'SH01-GanBanMian',
    '干拌面',
    '干拌宽面、烧椒面（可切汤/干拌）',
    7,
    1,
    NULL,
    NULL,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `product_categories`
VALUES (
    3,
    1,
    'SH01-ChaoShouShuiJiao',
    '抄手·水饺',
    '清汤/红油抄手与红油水饺',
    8,
    1,
    NULL,
    NULL,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `product_categories`
VALUES (
    4,
    1,
    'SH01-TangYuan',
    '甜香汤圆',
    '汤圆荷包蛋、红糖小汤圆（甜品，咸甜分开）',
    9,
    1,
    NULL,
    NULL,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `product_categories`
VALUES (
    5,
    1,
    'SH01-BaoZiDianXin',
    '包子·点心',
    '香葱包、酱肉包，支持单个起售',
    10,
    1,
    NULL,
    NULL,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- ----------------------------
-- Table structure for product_feedbacks
-- ----------------------------
DROP TABLE IF EXISTS `product_feedbacks`;
CREATE TABLE `product_feedbacks` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '商品 → products.id',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '评价发生门店 → stores.id',
  `verdicts` json NULL COMMENT '白名单标签，如 [\"TOO_SPICY\",\"DISLIKE\"] / [\"TASTY\"]',
  `sentiment` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'NEUTRAL' COMMENT '情感：POSITIVE/NEGATIVE/NEUTRAL',
  `remark` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '备注',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_pf_order_product_customer`(
    `order_id` ASC,
    `product_id` ASC,
    `customer_id` ASC
  ) USING BTREE,
  INDEX `idx_pf_customer`(`customer_id` ASC) USING BTREE,
  INDEX `idx_pf_store`(`store_id` ASC) USING BTREE,
  INDEX `fk_pf_product`(`product_id` ASC) USING BTREE,
  CONSTRAINT `fk_pf_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_pf_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_pf_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '商品轻量评价表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of product_feedbacks
-- ----------------------------
-- ----------------------------
-- Table structure for product_images
-- ----------------------------
DROP TABLE IF EXISTS `product_images`;
CREATE TABLE `product_images` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '商品 → products.id',
  `image_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '图片 URL',
  `image_type` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'DETAIL' COMMENT 'MAIN=主图 | DETAIL=详情图 | OPTION=规格示意图',
  `sort_order` int NOT NULL DEFAULT 0 COMMENT '同类型内排序',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_pi_product`(`product_id` ASC, `sort_order` ASC) USING BTREE,
  CONSTRAINT `fk_pi_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '商品图片表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of product_images
-- ----------------------------
-- ----------------------------
-- Table structure for product_option_groups
-- ----------------------------
DROP TABLE IF EXISTS `product_option_groups`;
CREATE TABLE `product_option_groups` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '商品 → products.id',
  `group_id` bigint UNSIGNED NOT NULL COMMENT '规格组 → option_groups.id',
  `sort_order` int NOT NULL DEFAULT 0 COMMENT '该商品下规格组展示顺序',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_product_group`(`product_id` ASC, `group_id` ASC) USING BTREE,
  INDEX `fk_pog_group`(`group_id` ASC) USING BTREE,
  CONSTRAINT `fk_pog_group` FOREIGN KEY (`group_id`) REFERENCES `option_groups` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_pog_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 92 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '商品-规格组关联表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of product_option_groups
-- ----------------------------
INSERT INTO `product_option_groups`
VALUES (1, 6, 1, 7);
INSERT INTO `product_option_groups`
VALUES (2, 5, 1, 7);
INSERT INTO `product_option_groups`
VALUES (3, 4, 1, 7);
INSERT INTO `product_option_groups`
VALUES (4, 3, 1, 7);
INSERT INTO `product_option_groups`
VALUES (5, 2, 1, 7);
INSERT INTO `product_option_groups`
VALUES (6, 1, 1, 7);
INSERT INTO `product_option_groups`
VALUES (7, 8, 1, 7);
INSERT INTO `product_option_groups`
VALUES (8, 7, 1, 7);
INSERT INTO `product_option_groups`
VALUES (9, 6, 2, 8);
INSERT INTO `product_option_groups`
VALUES (10, 5, 2, 8);
INSERT INTO `product_option_groups`
VALUES (11, 4, 2, 8);
INSERT INTO `product_option_groups`
VALUES (12, 3, 2, 8);
INSERT INTO `product_option_groups`
VALUES (13, 2, 2, 8);
INSERT INTO `product_option_groups`
VALUES (14, 1, 2, 8);
INSERT INTO `product_option_groups`
VALUES (15, 8, 2, 8);
INSERT INTO `product_option_groups`
VALUES (16, 7, 2, 8);
INSERT INTO `product_option_groups`
VALUES (17, 6, 3, 9);
INSERT INTO `product_option_groups`
VALUES (18, 5, 3, 9);
INSERT INTO `product_option_groups`
VALUES (19, 4, 3, 9);
INSERT INTO `product_option_groups`
VALUES (20, 3, 3, 9);
INSERT INTO `product_option_groups`
VALUES (21, 2, 3, 9);
INSERT INTO `product_option_groups`
VALUES (22, 1, 3, 9);
INSERT INTO `product_option_groups`
VALUES (23, 8, 3, 9);
INSERT INTO `product_option_groups`
VALUES (24, 7, 3, 9);
INSERT INTO `product_option_groups`
VALUES (25, 6, 4, 10);
INSERT INTO `product_option_groups`
VALUES (26, 5, 4, 10);
INSERT INTO `product_option_groups`
VALUES (27, 4, 4, 10);
INSERT INTO `product_option_groups`
VALUES (28, 3, 4, 10);
INSERT INTO `product_option_groups`
VALUES (29, 2, 4, 10);
INSERT INTO `product_option_groups`
VALUES (30, 1, 4, 10);
INSERT INTO `product_option_groups`
VALUES (31, 8, 4, 10);
INSERT INTO `product_option_groups`
VALUES (32, 7, 4, 10);
INSERT INTO `product_option_groups`
VALUES (33, 6, 5, 11);
INSERT INTO `product_option_groups`
VALUES (34, 5, 5, 11);
INSERT INTO `product_option_groups`
VALUES (35, 4, 5, 11);
INSERT INTO `product_option_groups`
VALUES (36, 3, 5, 11);
INSERT INTO `product_option_groups`
VALUES (37, 2, 5, 11);
INSERT INTO `product_option_groups`
VALUES (38, 1, 5, 11);
INSERT INTO `product_option_groups`
VALUES (39, 8, 5, 11);
INSERT INTO `product_option_groups`
VALUES (40, 7, 5, 11);
INSERT INTO `product_option_groups`
VALUES (41, 6, 6, 12);
INSERT INTO `product_option_groups`
VALUES (42, 5, 6, 12);
INSERT INTO `product_option_groups`
VALUES (43, 4, 6, 12);
INSERT INTO `product_option_groups`
VALUES (44, 3, 6, 12);
INSERT INTO `product_option_groups`
VALUES (45, 2, 6, 12);
INSERT INTO `product_option_groups`
VALUES (46, 1, 6, 12);
INSERT INTO `product_option_groups`
VALUES (47, 8, 6, 12);
INSERT INTO `product_option_groups`
VALUES (48, 7, 6, 12);
INSERT INTO `product_option_groups`
VALUES (64, 10, 1, 7);
INSERT INTO `product_option_groups`
VALUES (65, 9, 1, 7);
INSERT INTO `product_option_groups`
VALUES (66, 10, 6, 12);
INSERT INTO `product_option_groups`
VALUES (67, 9, 6, 12);
INSERT INTO `product_option_groups`
VALUES (68, 10, 7, 13);
INSERT INTO `product_option_groups`
VALUES (69, 9, 7, 13);
INSERT INTO `product_option_groups`
VALUES (70, 10, 8, 14);
INSERT INTO `product_option_groups`
VALUES (71, 9, 8, 14);
INSERT INTO `product_option_groups`
VALUES (79, 11, 9, 15);
INSERT INTO `product_option_groups`
VALUES (80, 11, 10, 16);
INSERT INTO `product_option_groups`
VALUES (81, 11, 11, 17);
INSERT INTO `product_option_groups`
VALUES (82, 12, 9, 15);
INSERT INTO `product_option_groups`
VALUES (83, 12, 11, 17);
INSERT INTO `product_option_groups`
VALUES (85, 14, 12, 18);
INSERT INTO `product_option_groups`
VALUES (86, 13, 12, 18);
INSERT INTO `product_option_groups`
VALUES (87, 14, 13, 19);
INSERT INTO `product_option_groups`
VALUES (88, 13, 13, 19);
-- ----------------------------
-- Table structure for product_taste_profiles
-- ----------------------------
DROP TABLE IF EXISTS `product_taste_profiles`;
CREATE TABLE `product_taste_profiles` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '商品 → products.id',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `dimensions` json NULL COMMENT '口味维度分，如 {\"spicy\":2,\"sweetness\":0,\"numbing\":0}',
  `tags` json NULL COMMENT '口味标签，如 [\"劲道\",\"酸辣\",\"牛肉\"]',
  `source` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'AUTO' COMMENT '来源：AUTO=自动推导 MANUAL=人工微调',
  `manual_overrides` json NULL COMMENT '人工接管的键；AUTO 重算时跳过',
  `derived_from` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '推导依据说明',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_ptp_product`(`product_id` ASC) USING BTREE,
  INDEX `idx_ptp_store`(`store_id` ASC) USING BTREE,
  CONSTRAINT `fk_ptp_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_ptp_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 15 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '产品口味画像表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of product_taste_profiles
-- ----------------------------
INSERT INTO `product_taste_profiles`
VALUES (
    1,
    1,
    1,
    '{\"spicy\": 2, \"numbing\": 2}',
    '[\"麻辣\"]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    'name:spicy,name:numbing,tag:麻辣',
    '2026-09-21 09:36:34.576',
    '2026-09-21 09:36:34.576'
  );
INSERT INTO `product_taste_profiles`
VALUES (
    2,
    7,
    1,
    '{}',
    '[]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    '',
    '2026-09-21 09:36:34.589',
    '2026-09-21 09:36:34.589'
  );
INSERT INTO `product_taste_profiles`
VALUES (
    3,
    9,
    1,
    '{\"spicy\": 2, \"sourness\": 1}',
    '[\"酸辣\", \"清汤\"]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    'name:spicy,name:sourness,tag:酸辣,tag:清汤',
    '2026-09-21 09:36:34.600',
    '2026-09-21 09:36:34.600'
  );
INSERT INTO `product_taste_profiles`
VALUES (
    4,
    11,
    1,
    '{\"sweetness\": 2}',
    '[]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    'option:sweetness',
    '2026-09-21 09:36:34.608',
    '2026-09-21 09:36:34.608'
  );
INSERT INTO `product_taste_profiles`
VALUES (
    5,
    13,
    1,
    '{}',
    '[]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    '',
    '2026-09-21 09:36:34.617',
    '2026-09-21 09:36:34.617'
  );
INSERT INTO `product_taste_profiles`
VALUES (
    6,
    2,
    1,
    '{}',
    '[]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    '',
    '2026-09-21 09:36:34.626',
    '2026-09-21 09:36:34.626'
  );
INSERT INTO `product_taste_profiles`
VALUES (
    7,
    8,
    1,
    '{\"spicy\": 2}',
    '[]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    'name:spicy',
    '2026-09-21 09:36:34.636',
    '2026-09-21 09:36:34.636'
  );
INSERT INTO `product_taste_profiles`
VALUES (
    8,
    10,
    1,
    '{\"spicy\": 2}',
    '[]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    'name:spicy',
    '2026-09-21 09:36:34.644',
    '2026-09-21 09:36:34.644'
  );
INSERT INTO `product_taste_profiles`
VALUES (
    9,
    12,
    1,
    '{\"sweetness\": 2}',
    '[]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    'option:sweetness',
    '2026-09-21 09:36:34.652',
    '2026-09-21 09:36:34.652'
  );
INSERT INTO `product_taste_profiles`
VALUES (
    10,
    14,
    1,
    '{}',
    '[]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    '',
    '2026-09-21 09:36:34.661',
    '2026-09-21 09:36:34.661'
  );
INSERT INTO `product_taste_profiles`
VALUES (
    11,
    3,
    1,
    '{}',
    '[\"软糯\"]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    'tag:软糯',
    '2026-09-21 09:36:34.671',
    '2026-09-21 09:36:34.671'
  );
INSERT INTO `product_taste_profiles`
VALUES (
    12,
    4,
    1,
    '{}',
    '[\"牛肉\", \"红烧\"]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    'tag:牛肉,tag:红烧',
    '2026-09-21 09:36:34.680',
    '2026-09-21 09:36:34.680'
  );
INSERT INTO `product_taste_profiles`
VALUES (
    13,
    5,
    1,
    '{}',
    '[]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    '',
    '2026-09-21 09:36:34.689',
    '2026-09-21 09:36:34.689'
  );
INSERT INTO `product_taste_profiles`
VALUES (
    14,
    6,
    1,
    '{}',
    '[]',
    'AUTO',
    '{\"dim\": [], \"tags_add\": [], \"tags_remove\": []}',
    '',
    '2026-09-21 09:36:34.697',
    '2026-09-21 09:36:34.697'
  );
-- ----------------------------
-- Table structure for products
-- ----------------------------
DROP TABLE IF EXISTS `products`;
CREATE TABLE `products` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '所属门店 → stores.id',
  `category_id` bigint UNSIGNED NOT NULL COMMENT '分类 → product_categories.id',
  `product_no` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '店内商品编号：{store_no|0000}-分类逻辑-商品逻辑，如 SH01-Mian-NiuRouMian',
  `name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '商品名称',
  `description` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL COMMENT '商品描述/卖点',
  `main_image` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '主图相对或绝对路径',
  `price` decimal(10, 2) NOT NULL COMMENT '基础售价（不含规格加价）',
  `original_price` decimal(10, 2) NULL DEFAULT NULL COMMENT '划线原价（可选）',
  `stock` int NOT NULL DEFAULT 999 COMMENT '默认库存；999 表示不限量',
  `box_price` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '单份餐盒/打包费',
  `min_order_count` int NOT NULL DEFAULT 1 COMMENT '起售份数',
  `require_category_ids` json NULL COMMENT '须先点过的分类 id（OR）；NULL=继承所属分类默认',
  `require_product_ids` json NULL COMMENT '须先点过的指定商品 id（OR）；与分类依赖合并 OR',
  `status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'ON_SALE' COMMENT 'ON_SALE=在售 | OFF_SALE=停售 | SOLD_OUT=售罄',
  `is_recommended` tinyint NOT NULL DEFAULT 0 COMMENT '是否推荐：1=是 0=否',
  `is_hot` tinyint NOT NULL DEFAULT 0 COMMENT '是否热销标签：1=是 0=否',
  `no_member_discount` tinyint NOT NULL DEFAULT 0 COMMENT '特价/不参与会员折扣：1=是 0=否',
  `no_cooking` tinyint NOT NULL DEFAULT 0 COMMENT '免烹饪/现成品（卤味等）：1=KDS可直出餐 0=需正常制作',
  `sort_order` int NOT NULL DEFAULT 0 COMMENT '分类内排序',
  `sales_count` int NOT NULL DEFAULT 0 COMMENT '累计销量（冗余统计）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_products_store_no`(`store_id` ASC, `product_no` ASC) USING BTREE,
  INDEX `idx_products_store`(`store_id` ASC, `status` ASC, `sort_order` ASC) USING BTREE,
  INDEX `idx_products_category`(
    `category_id` ASC,
    `status` ASC,
    `sort_order` ASC
  ) USING BTREE,
  CONSTRAINT `fk_products_category` FOREIGN KEY (`category_id`) REFERENCES `product_categories` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_products_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `chk_products_orig_price` CHECK (
    (`original_price` is null)
    or (`original_price` >= 0)
  ),
  CONSTRAINT `chk_products_price_pos` CHECK (`price` > 0),
  CONSTRAINT `chk_products_stock_nonneg` CHECK (`stock` >= 0)
) ENGINE = InnoDB AUTO_INCREMENT = 15 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '商品表（按门店）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of products
-- ----------------------------
INSERT INTO `products`
VALUES (
    1,
    1,
    1,
    'SH01-ZhaoPaiMian-JiZaMian',
    '鸡杂面',
    '爆炒鸡杂（鸡胗/鸡心）浇头，红汤细面，麻辣鲜香',
    '/uploads/products/jizamian.png',
    9.00,
    NULL,
    9999,
    1.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    0,
    0,
    0,
    0,
    0,
    0,
    '2026-09-20 15:04:33.201',
    '2026-09-20 15:04:33.201'
  );
INSERT INTO `products`
VALUES (
    2,
    1,
    1,
    'SH01-ZhaoPaiMian-ZaJiangMian',
    '杂酱面',
    '肉末杂酱臊子，酱色油亮，经典川味干香',
    '/uploads/products/zajiangmian.png',
    8.00,
    NULL,
    9999,
    1.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    0,
    1,
    0,
    0,
    1,
    0,
    '2026-09-20 15:04:33.206',
    '2026-09-20 15:04:33.206'
  );
INSERT INTO `products`
VALUES (
    3,
    1,
    1,
    'SH01-ZhaoPaiMian-WanZaMian',
    '豌杂面',
    '豌豆+杂酱双浇头，软糯耙豌豆配肉末（即“碗杂面”）',
    '/uploads/products/wanzamian.png',
    9.00,
    NULL,
    9999,
    1.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    0,
    0,
    0,
    0,
    2,
    0,
    '2026-09-20 15:04:33.208',
    '2026-09-20 15:04:33.208'
  );
INSERT INTO `products`
VALUES (
    4,
    1,
    1,
    'SH01-ZhaoPaiMian-SunZiNiuRouMian',
    '笋子牛肉面',
    '红烧牛肉块配鲜笋，红汤醇厚，大块牛肉满足',
    '/uploads/products/sunziniuroumian.png',
    12.00,
    NULL,
    9999,
    1.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    0,
    1,
    0,
    0,
    3,
    0,
    '2026-09-20 15:04:33.211',
    '2026-09-20 15:04:33.211'
  );
INSERT INTO `products`
VALUES (
    5,
    1,
    1,
    'SH01-ZhaoPaiMian-TiHuaMian',
    '蹄花面',
    '炖耙糯猪蹄花，雪豆汤底，胶质浓稠',
    '/uploads/products/tihuamian.png',
    14.00,
    NULL,
    9999,
    1.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    0,
    0,
    0,
    0,
    4,
    0,
    '2026-09-20 15:04:33.214',
    '2026-09-20 15:04:33.214'
  );
INSERT INTO `products`
VALUES (
    6,
    1,
    1,
    'SH01-ZhaoPaiMian-ChangWangMian',
    '肠旺面',
    '★招牌：卤肥肠+血旺（鸭血）双浇，红油汤底，射洪必点',
    '/uploads/products/changwangmian.png',
    12.00,
    NULL,
    9999,
    1.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    1,
    1,
    0,
    0,
    5,
    0,
    '2026-09-20 15:04:33.217',
    '2026-09-20 15:04:33.217'
  );
INSERT INTO `products`
VALUES (
    7,
    1,
    2,
    'SH01-GanBanMian-GanBanKuanMian',
    '干拌宽面',
    '宽面干拌，配杂酱/芽菜/花生碎，无汤更香',
    '/uploads/products/ganbankuanmian.png',
    8.00,
    NULL,
    9999,
    1.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    0,
    0,
    0,
    0,
    0,
    0,
    '2026-09-20 15:04:33.219',
    '2026-09-20 15:04:33.219'
  );
INSERT INTO `products`
VALUES (
    8,
    1,
    2,
    'SH01-GanBanMian-ShaoJiaoMian',
    '烧椒面',
    '炭烧青椒剁碎拌面，糊辣清香（可选汤/干拌）',
    '/uploads/products/shaojiaomian.png',
    9.00,
    NULL,
    9999,
    1.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    0,
    0,
    0,
    0,
    1,
    0,
    '2026-09-20 15:04:33.222',
    '2026-09-20 15:04:33.222'
  );
INSERT INTO `products`
VALUES (
    9,
    1,
    3,
    'SH01-ChaoShouShuiJiao-ChaoShou',
    '抄手',
    '薄皮猪肉抄手，可选清汤/红油/酸辣/干拌，皮薄馅嫩',
    '/uploads/products/chaoshou.png',
    8.00,
    NULL,
    9999,
    1.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    0,
    1,
    0,
    0,
    0,
    0,
    '2026-09-20 15:04:33.224',
    '2026-09-20 15:04:33.224'
  );
INSERT INTO `products`
VALUES (
    10,
    1,
    3,
    'SH01-ChaoShouShuiJiao-HongYouShuiJiao',
    '红油水饺',
    '皮薄馅大水饺，淋红亮辣椒油与复制酱油，撒芝麻葱花',
    '/uploads/products/hongyoushuijiao.png',
    9.00,
    NULL,
    9999,
    1.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    0,
    0,
    0,
    0,
    1,
    0,
    '2026-09-20 15:04:33.228',
    '2026-09-20 15:04:33.228'
  );
INSERT INTO `products`
VALUES (
    11,
    1,
    4,
    'SH01-TangYuan-TangYuanHeBaoDan',
    '汤圆荷包蛋',
    '红糖水小汤圆配煎荷包蛋，甜香暖胃（早餐/夜宵）',
    '/uploads/products/tangyuanhebaodan.png',
    7.00,
    NULL,
    9999,
    1.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    0,
    0,
    0,
    0,
    0,
    0,
    '2026-09-20 15:04:33.231',
    '2026-09-20 15:04:33.231'
  );
INSERT INTO `products`
VALUES (
    12,
    1,
    4,
    'SH01-TangYuan-HongTangXiaoTangYuan',
    '红糖小汤圆',
    '糯米小汤圆卧浓稠红糖汁，撒熟白芝麻，甜而不腻',
    '/uploads/products/hongtangxiaotangyuan.png',
    6.00,
    NULL,
    9999,
    1.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    0,
    0,
    0,
    0,
    1,
    0,
    '2026-09-20 15:04:33.233',
    '2026-09-20 15:04:33.233'
  );
INSERT INTO `products`
VALUES (
    13,
    1,
    5,
    'SH01-BaoZiDianXin-XiangCongBao',
    '香葱包',
    '白白胖胖，面皮里裹新鲜香葱碎，咸香松软（1个起售）',
    '/uploads/products/xiangcongbao.png',
    2.00,
    NULL,
    9999,
    0.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    0,
    0,
    0,
    0,
    0,
    0,
    '2026-09-20 15:04:33.236',
    '2026-09-20 15:04:33.236'
  );
INSERT INTO `products`
VALUES (
    14,
    1,
    5,
    'SH01-BaoZiDianXin-JiangRouBao',
    '酱肉包',
    '酱色猪肉馅，咸甜适口，汁香浓郁（1个起售）',
    '/uploads/products/jiangroubao.png',
    2.50,
    NULL,
    9999,
    0.00,
    1,
    NULL,
    NULL,
    'ON_SALE',
    0,
    0,
    0,
    0,
    1,
    0,
    '2026-09-20 15:04:33.238',
    '2026-09-20 15:04:33.238'
  );
-- ----------------------------
-- Table structure for role_permissions
-- ----------------------------
DROP TABLE IF EXISTS `role_permissions`;
CREATE TABLE `role_permissions` (
  `role_id` bigint UNSIGNED NOT NULL COMMENT '角色 ID → roles.id',
  `permission_id` bigint UNSIGNED NOT NULL COMMENT '权限 ID → permissions.id',
  PRIMARY KEY (`role_id`, `permission_id`) USING BTREE,
  INDEX `fk_rp_perm`(`permission_id` ASC) USING BTREE,
  CONSTRAINT `fk_rp_perm` FOREIGN KEY (`permission_id`) REFERENCES `permissions` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_rp_role` FOREIGN KEY (`role_id`) REFERENCES `roles` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '角色-权限关联表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of role_permissions
-- ----------------------------
INSERT INTO `role_permissions`
VALUES (1, 1);
INSERT INTO `role_permissions`
VALUES (2, 1);
INSERT INTO `role_permissions`
VALUES (3, 1);
INSERT INTO `role_permissions`
VALUES (5, 1);
INSERT INTO `role_permissions`
VALUES (1, 2);
INSERT INTO `role_permissions`
VALUES (2, 2);
INSERT INTO `role_permissions`
VALUES (5, 2);
INSERT INTO `role_permissions`
VALUES (1, 3);
INSERT INTO `role_permissions`
VALUES (2, 3);
INSERT INTO `role_permissions`
VALUES (5, 3);
INSERT INTO `role_permissions`
VALUES (1, 4);
INSERT INTO `role_permissions`
VALUES (2, 4);
INSERT INTO `role_permissions`
VALUES (5, 4);
INSERT INTO `role_permissions`
VALUES (1, 5);
INSERT INTO `role_permissions`
VALUES (2, 5);
INSERT INTO `role_permissions`
VALUES (3, 5);
INSERT INTO `role_permissions`
VALUES (4, 5);
INSERT INTO `role_permissions`
VALUES (5, 5);
INSERT INTO `role_permissions`
VALUES (1, 6);
INSERT INTO `role_permissions`
VALUES (2, 6);
INSERT INTO `role_permissions`
VALUES (3, 6);
INSERT INTO `role_permissions`
VALUES (4, 6);
INSERT INTO `role_permissions`
VALUES (1, 7);
INSERT INTO `role_permissions`
VALUES (2, 7);
INSERT INTO `role_permissions`
VALUES (3, 7);
INSERT INTO `role_permissions`
VALUES (5, 7);
INSERT INTO `role_permissions`
VALUES (1, 8);
INSERT INTO `role_permissions`
VALUES (2, 8);
INSERT INTO `role_permissions`
VALUES (1, 9);
INSERT INTO `role_permissions`
VALUES (2, 9);
INSERT INTO `role_permissions`
VALUES (1, 10);
INSERT INTO `role_permissions`
VALUES (2, 10);
INSERT INTO `role_permissions`
VALUES (1, 11);
INSERT INTO `role_permissions`
VALUES (2, 11);
INSERT INTO `role_permissions`
VALUES (1, 12);
INSERT INTO `role_permissions`
VALUES (2, 12);
INSERT INTO `role_permissions`
VALUES (3, 12);
INSERT INTO `role_permissions`
VALUES (5, 12);
INSERT INTO `role_permissions`
VALUES (1, 13);
INSERT INTO `role_permissions`
VALUES (2, 13);
INSERT INTO `role_permissions`
VALUES (1, 14);
INSERT INTO `role_permissions`
VALUES (2, 14);
INSERT INTO `role_permissions`
VALUES (1, 15);
INSERT INTO `role_permissions`
VALUES (2, 15);
INSERT INTO `role_permissions`
VALUES (1, 16);
INSERT INTO `role_permissions`
VALUES (2, 16);
INSERT INTO `role_permissions`
VALUES (1, 17);
INSERT INTO `role_permissions`
VALUES (1, 18);
INSERT INTO `role_permissions`
VALUES (2, 18);
INSERT INTO `role_permissions`
VALUES (1, 19);
INSERT INTO `role_permissions`
VALUES (2, 19);
INSERT INTO `role_permissions`
VALUES (1, 20);
INSERT INTO `role_permissions`
VALUES (2, 20);
-- ----------------------------
-- Table structure for roles
-- ----------------------------
DROP TABLE IF EXISTS `roles`;
CREATE TABLE `roles` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `code` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '角色编码：SUPER_ADMIN=超管 | STORE_ADMIN=店长 | CASHIER=收银 | KITCHEN=后厨 | OPERATOR=运营',
  `name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '角色显示名称',
  `description` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '角色职责说明',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `code`(`code` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 6 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '后台角色表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of roles
-- ----------------------------
INSERT INTO `roles`
VALUES (
    1,
    'SUPER_ADMIN',
    '超级管理员',
    '跨店全部权限',
    '2026-09-20 15:04:32.970',
    '2026-09-20 15:04:32.970'
  );
INSERT INTO `roles`
VALUES (
    2,
    'STORE_ADMIN',
    '门店管理员',
    '本店运营（含退款；不含全局系统配置）',
    '2026-09-20 15:04:32.975',
    '2026-09-20 15:04:32.975'
  );
INSERT INTO `roles`
VALUES (
    3,
    'CASHIER',
    '收银员',
    '订单、支付与收银点餐（不可退款）',
    '2026-09-20 15:04:32.977',
    '2026-09-20 15:04:32.977'
  );
INSERT INTO `roles`
VALUES (
    4,
    'KITCHEN',
    '厨房',
    'KDS 出餐（不可退款/不可审外卖退款）',
    '2026-09-20 15:04:32.980',
    '2026-09-20 15:04:32.980'
  );
INSERT INTO `roles`
VALUES (
    5,
    'OPERATOR',
    '运营',
    '商品与数据（不可退款）',
    '2026-09-20 15:04:32.982',
    '2026-09-20 15:04:32.982'
  );
-- ----------------------------
-- Table structure for store_business_hours
-- ----------------------------
DROP TABLE IF EXISTS `store_business_hours`;
CREATE TABLE `store_business_hours` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `weekday` tinyint NOT NULL COMMENT '星期几：1=周一 ... 7=周日',
  `slot_index` int NOT NULL DEFAULT 0 COMMENT '当日第几时段，从 0 开始',
  `open_time` time NOT NULL COMMENT '营业开始时间',
  `close_time` time NOT NULL COMMENT '营业结束时间（<=开始时间视为跨午夜）',
  `cross_day` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否跨午夜',
  `is_open` tinyint(1) NOT NULL DEFAULT 1 COMMENT '该时段是否营业',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_sbh_store_day_slot`(`store_id` ASC, `weekday` ASC, `slot_index` ASC) USING BTREE,
  INDEX `idx_sbh_store`(`store_id` ASC) USING BTREE,
  CONSTRAINT `fk_sbh_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '门店营业时间（多时段/跨午夜）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of store_business_hours
-- ----------------------------
-- ----------------------------
-- Table structure for store_product_stocks
-- ----------------------------
DROP TABLE IF EXISTS `store_product_stocks`;
CREATE TABLE `store_product_stocks` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '商品 → products.id',
  `stock` int NOT NULL DEFAULT 999 COMMENT '本店库存覆盖；999=不限',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '本店售卖状态：1=在售 0=本店停售',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_store_product`(`store_id` ASC, `product_id` ASC) USING BTREE,
  INDEX `idx_sps_product`(`product_id` ASC) USING BTREE,
  CONSTRAINT `fk_sps_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_sps_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `chk_sps_stock_nonneg` CHECK (`stock` >= 0)
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '分店商品库存覆盖表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of store_product_stocks
-- ----------------------------
-- ----------------------------
-- Table structure for store_settings
-- ----------------------------
DROP TABLE IF EXISTS `store_settings`;
CREATE TABLE `store_settings` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `setting_key` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '参数键，如 packing_fee_enabled',
  `setting_value` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '参数值（字符串存储，由业务解析类型）',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '最后修改时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_store_setting`(`store_id` ASC, `setting_key` ASC) USING BTREE,
  CONSTRAINT `fk_ss_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '门店业务参数表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of store_settings
-- ----------------------------
-- ----------------------------
-- Table structure for stores
-- ----------------------------
DROP TABLE IF EXISTS `stores`;
CREATE TABLE `stores` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_no` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '门店编号（写入业务单号前缀，如 TH01）',
  `name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '门店名称',
  `address` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '门店地址',
  `phone` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '门店联系电话',
  `latitude` decimal(10, 7) NULL DEFAULT NULL COMMENT '纬度（天气口味加权）',
  `longitude` decimal(10, 7) NULL DEFAULT NULL COMMENT '经度（天气口味加权）',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '营业状态：1=营业中 0=停业/禁用',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `store_no`(`store_no` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 3 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '门店表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of stores
-- ----------------------------
INSERT INTO `stores`
VALUES (
    1,
    'SH01',
    '肠旺面(总店)',
    '城南红砖路88号',
    '151-9691-4600',
    30.8719000,
    105.3884000,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
INSERT INTO `stores`
VALUES (
    2,
    'SH02',
    '肠旺面(二店)',
    '城南红砖路188号',
    '151-9691-4600',
    30.8719000,
    105.3884000,
    1,
    '2026-08-18 18:58:58.888',
    '2026-08-18 18:58:58.888'
  );
-- ----------------------------
-- Table structure for system_settings
-- ----------------------------
DROP TABLE IF EXISTS `system_settings`;
CREATE TABLE `system_settings` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `setting_key` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '配置键',
  `setting_value` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '配置值',
  `description` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '配置说明',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '最后修改时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `setting_key`(`setting_key` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 38 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '系统全局配置表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of system_settings
-- ----------------------------
INSERT INTO `system_settings`
VALUES (
    1,
    'order_payment_timeout_minutes',
    '15',
    '订单支付超时分钟数',
    '2026-09-20 15:04:33.067'
  );
INSERT INTO `system_settings`
VALUES (
    2,
    'visitor_data_retention_days',
    '7',
    '匿名访客数据保留天数',
    '2026-09-20 15:04:33.071'
  );
INSERT INTO `system_settings`
VALUES (
    3,
    'welcome_cooldown_seconds',
    '60',
    '迎宾冷却秒数',
    '2026-09-20 15:04:33.073'
  );
INSERT INTO `system_settings`
VALUES (
    4,
    'face_match_threshold',
    '0.75',
    '人脸匹配阈值',
    '2026-09-20 15:04:33.075'
  );
INSERT INTO `system_settings`
VALUES (
    5,
    'face_confirm_frames',
    '5',
    '人脸确认帧数',
    '2026-09-20 15:04:33.077'
  );
INSERT INTO `system_settings`
VALUES (
    6,
    'operation_log_retention_days',
    '60',
    '操作日志保留天数',
    '2026-09-20 15:04:33.080'
  );
INSERT INTO `system_settings`
VALUES (
    7,
    'kitchen_auto_complete_minutes',
    '30',
    '厨房 KDS 未处理自动出餐（分钟，0=关闭）',
    '2026-09-20 15:04:33.082'
  );
INSERT INTO `system_settings`
VALUES (
    8,
    'delivery_auto_confirm',
    'false',
    '新外卖单是否自动向平台 confirm',
    '2026-09-20 15:04:33.085'
  );
INSERT INTO `system_settings`
VALUES (
    9,
    'welcome_coupon_name',
    '新客满30减5',
    '注册赠券模板名',
    '2026-09-20 15:04:33.087'
  );
INSERT INTO `system_settings`
VALUES (
    10,
    'enable_alipay_avatar',
    'true',
    '支付宝头像能力',
    '2026-09-20 15:04:33.089'
  );
INSERT INTO `system_settings`
VALUES (
    11,
    'enable_alipay_nickname',
    'true',
    '支付宝昵称能力',
    '2026-09-20 15:04:33.091'
  );
INSERT INTO `system_settings`
VALUES (
    12,
    'enable_alipay_phone_bind',
    'false',
    '支付宝手机号绑定',
    '2026-09-20 15:04:33.092'
  );
INSERT INTO `system_settings`
VALUES (
    13,
    'enable_wx_avatar',
    'true',
    '微信头像能力',
    '2026-09-20 15:04:33.095'
  );
INSERT INTO `system_settings`
VALUES (
    14,
    'enable_wx_nickname',
    'true',
    '微信昵称能力',
    '2026-09-20 15:04:33.097'
  );
INSERT INTO `system_settings`
VALUES (
    15,
    'enable_wx_phone_bind',
    'false',
    '微信手机号绑定',
    '2026-09-20 15:04:33.100'
  );
INSERT INTO `system_settings`
VALUES (
    16,
    'max_upload_size_mb',
    '5',
    '单文件上传上限 MB',
    '2026-09-20 15:04:33.102'
  );
INSERT INTO `system_settings`
VALUES (
    17,
    'jwt_expire_minutes',
    '1440',
    '员工 Token 有效期（分钟）',
    '2026-09-20 15:04:33.104'
  );
INSERT INTO `system_settings`
VALUES (
    18,
    'customer_jwt_expire_minutes',
    '1440',
    '顾客 Token 有效期（分钟）',
    '2026-09-20 15:04:33.106'
  );
INSERT INTO `system_settings`
VALUES (
    19,
    'webhook_ip_allowlist',
    '',
    'Webhook IP 白名单',
    '2026-09-20 15:04:33.107'
  );
INSERT INTO `system_settings`
VALUES (
    20,
    'webhook_require_allowlist',
    'false',
    '强制 Webhook 白名单',
    '2026-09-20 15:04:33.109'
  );
INSERT INTO `system_settings`
VALUES (
    21,
    'trusted_proxy_ips',
    '127.0.0.1,::1',
    '可信反代 IP',
    '2026-09-20 15:04:33.112'
  );
INSERT INTO `system_settings`
VALUES (
    22,
    'h5_public_base_url',
    '',
    'H5 对外域名',
    '2026-09-20 15:04:33.114'
  );
INSERT INTO `system_settings`
VALUES (
    23,
    'dev_lan_host',
    '',
    '开发机局域网 IP',
    '2026-09-20 15:04:33.117'
  );
INSERT INTO `system_settings`
VALUES (
    24,
    'mini_program_env_version',
    'auto',
    '小程序码版本',
    '2026-09-20 15:04:33.119'
  );
INSERT INTO `system_settings`
VALUES (
    25,
    'xpyun_enabled',
    'false',
    '厨房打印总开关',
    '2026-09-20 15:04:33.121'
  );
INSERT INTO `system_settings`
VALUES (
    26,
    'printer_mode',
    'local',
    '打印机模式',
    '2026-09-20 15:04:33.122'
  );
INSERT INTO `system_settings`
VALUES (
    27,
    'kitchen_ticket_order_no',
    'barcode',
    '厨房票订单号样式 barcode/text/off',
    '2026-09-20 15:04:33.124'
  );
INSERT INTO `system_settings`
VALUES (
    28,
    'xpyun_print_copies',
    '1',
    '小票打印份数',
    '2026-09-20 15:04:33.126'
  );
INSERT INTO `system_settings`
VALUES (
    29,
    'xpyun_voice',
    '0',
    '云打印语音 0~3',
    '2026-09-20 15:04:33.128'
  );
INSERT INTO `system_settings`
VALUES (
    30,
    'printer_host',
    '192.168.1.150',
    '打印机 IP',
    '2026-09-20 15:04:33.130'
  );
INSERT INTO `system_settings`
VALUES (
    31,
    'printer_port',
    '9100',
    '打印机端口',
    '2026-09-20 15:04:33.132'
  );
INSERT INTO `system_settings`
VALUES (
    32,
    'wechat_subscribe_template_order_ready',
    '',
    '微信出餐订阅模板',
    '2026-09-20 15:04:33.134'
  );
INSERT INTO `system_settings`
VALUES (
    33,
    'allowed_image_extensions',
    'jpg,jpeg,png,webp,gif',
    '允许的图片扩展名',
    '2026-09-20 15:04:33.136'
  );
INSERT INTO `system_settings`
VALUES (
    34,
    'app_timezone',
    'Asia/Shanghai',
    '业务时区',
    '2026-09-20 15:04:33.138'
  );
INSERT INTO `system_settings`
VALUES (
    35,
    'delivery_env_fallback_store_id',
    '0',
    '外卖 .env 兜底门店',
    '2026-09-20 15:04:33.140'
  );
INSERT INTO `system_settings`
VALUES (
    36,
    'preference_scene_enabled',
    'true',
    '口味场景加权（季节/同星期同餐段）',
    '2026-09-20 15:04:33.142'
  );
INSERT INTO `system_settings`
VALUES (
    37,
    'weather_pref_enabled',
    'true',
    '天气口味加权（需门店经纬度）',
    '2026-09-20 15:04:33.144'
  );
-- ----------------------------
-- Table structure for tables
-- ----------------------------
DROP TABLE IF EXISTS `tables`;
CREATE TABLE `tables` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '所属门店 → stores.id',
  `table_no` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '桌号编码（店内唯一，如 01、A12）',
  `table_name` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '顾客端展示名（如 1号桌、包厢A）',
  `qr_token` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '扫码令牌；正式环境随机串，开发环境可用桌号作 token',
  `wechat_qr_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '微信小程序码落地路径或参数',
  `alipay_qr_url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '支付宝小程序码落地路径或参数',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '桌台状态：1=启用 0=禁用',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `qr_token`(`qr_token` ASC) USING BTREE,
  UNIQUE INDEX `uk_store_table`(`store_id` ASC, `table_no` ASC) USING BTREE,
  INDEX `idx_tables_token`(`qr_token` ASC) USING BTREE,
  CONSTRAINT `fk_tables_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 4 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '堂食桌台表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of tables
-- ----------------------------
INSERT INTO `tables`
VALUES (
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
  );
INSERT INTO `tables`
VALUES (
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
  );
INSERT INTO `tables`
VALUES (
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
-- ----------------------------
-- Table structure for users
-- ----------------------------
DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `username` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '登录用户名，全局唯一',
  `password_hash` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'bcrypt 等算法哈希后的密码，明文不入库',
  `display_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '界面显示姓名',
  `role_id` bigint UNSIGNED NOT NULL COMMENT '角色 ID → roles.id',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '绑定门店 ID；NULL=超管可跨店，非空=仅能操作该店数据',
  `token_version` int NOT NULL DEFAULT 0 COMMENT 'JWT 版本号；改密/踢下线时递增，使旧令牌失效',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '账号状态：1=启用 0=停用',
  `last_login_at` datetime(3) NULL DEFAULT NULL COMMENT '最近登录时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `username`(`username` ASC) USING BTREE,
  INDEX `idx_users_store`(`store_id` ASC) USING BTREE,
  INDEX `fk_users_role`(`role_id` ASC) USING BTREE,
  CONSTRAINT `fk_users_role` FOREIGN KEY (`role_id`) REFERENCES `roles` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 5 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '后台员工用户表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of users
-- ----------------------------
INSERT INTO `users`
VALUES (
    1,
    'admin',
    '$2b$12$38dPQAo/7seG5MQ9uCaLg.eysxss7vHFFOYSORWyAO5sNflaFgaQm',
    '系统管理员',
    1,
    NULL,
    0,
    1,
    '2026-09-21 09:37:57.807',
    '2026-08-18 18:58:58.888',
    '2026-09-21 09:37:57.000'
  );
INSERT INTO `users`
VALUES (
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
  );
INSERT INTO `users`
VALUES (
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
  );
INSERT INTO `users`
VALUES (
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
-- ----------------------------
-- Table structure for visitor_events
-- ----------------------------
DROP TABLE IF EXISTS `visitor_events`;
CREATE TABLE `visitor_events` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `visitor_no` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '匿名访客编号（非会员）',
  `camera_id` bigint UNSIGNED NOT NULL COMMENT '摄像头 → cameras.id',
  `event_type` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'ENTER=进入 | EXIT=离开 | PASS=路过',
  `event_time` datetime(3) NOT NULL COMMENT '事件发生时间',
  `snapshot_path` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '抓拍图存储路径',
  `expires_at` datetime(3) NOT NULL COMMENT '数据保留到期时间（到期可清理）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入库时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_ve_camera_time`(`camera_id` ASC, `event_time` ASC) USING BTREE,
  INDEX `idx_ve_expires`(`expires_at` ASC) USING BTREE,
  CONSTRAINT `fk_ve_camera` FOREIGN KEY (`camera_id`) REFERENCES `cameras` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '匿名客流事件表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of visitor_events
-- ----------------------------
-- ----------------------------
-- Table structure for webhook_inbox
-- ----------------------------
DROP TABLE IF EXISTS `webhook_inbox`;
CREATE TABLE `webhook_inbox` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `channel` varchar(30) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'WECHAT_PAY/ALIPAY/MEITUAN/ELEME',
  `event_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '平台事件唯一 ID（幂等）',
  `event_type` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '事件类型',
  `payload` mediumtext CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '原始报文',
  `headers_json` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL COMMENT 'HTTP 头 JSON',
  `status` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PROCESSING/DONE/FAILED',
  `error_message` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '处理失败原因',
  `attempt_count` int NOT NULL DEFAULT 0 COMMENT '消费尝试次数',
  `last_error` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '最近一次错误摘要',
  `next_retry_at` datetime(3) NULL DEFAULT NULL COMMENT '下次允许重试时间',
  `locked_at` datetime(3) NULL DEFAULT NULL COMMENT '消费者加锁时间',
  `locked_by` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL COMMENT '锁持有者标识（实例/worker）',
  `received_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '接收时间',
  `processed_at` datetime(3) NULL DEFAULT NULL COMMENT '处理完成时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_webhook_event`(`channel` ASC, `event_id` ASC) USING BTREE,
  INDEX `idx_wi_status`(`status` ASC, `received_at` ASC) USING BTREE,
  INDEX `idx_wi_retry`(`status` ASC, `next_retry_at` ASC) USING BTREE,
  INDEX `idx_wi_locked`(`locked_at` ASC, `locked_by` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = 'Webhook 异步收件箱' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of webhook_inbox
-- ----------------------------
-- ----------------------------
-- Table structure for welcome_logs
-- ----------------------------
DROP TABLE IF EXISTS `welcome_logs`;
CREATE TABLE `welcome_logs` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '被识别顾客 → customers.id',
  `camera_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '触发摄像头 → cameras.id',
  `welcome_text` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '播报/展示的欢迎语（可含口味推荐）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '迎宾时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_welcome_customer`(`customer_id` ASC, `created_at` ASC) USING BTREE,
  CONSTRAINT `fk_welcome_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '人脸迎宾记录表' ROW_FORMAT = Dynamic;
SET FOREIGN_KEY_CHECKS = 1;
-- 添加运行数据
-- 添加运行数据结束
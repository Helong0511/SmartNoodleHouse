-- 删除数据库
DROP DATABASE IF EXISTS `smart_noodle_house`;
-- 创建数据库
CREATE DATABASE IF NOT EXISTS `smart_noodle_house` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- 切换数据库
USE `smart_noodle_house`;
SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;
SET FOREIGN_KEY_CHECKS = 0;
-- BEGIN CANONICAL SCHEMA
-- （Alembic 0001_baseline 仅执行本标记内的 DDL，不执行上方删库/授权）
-- ----------------------------
-- Table structure for ab_assignments
-- ----------------------------
CREATE TABLE IF NOT EXISTS `ab_assignments` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `experiment_key` varchar(64) NOT NULL COMMENT '实验标识 → ab_experiments.experiment_key',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `bucket` varchar(40) NOT NULL COMMENT '分配到的桶编码',
  `assigned_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '分桶时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_aba_experiment_customer`(`experiment_key` ASC, `customer_id` ASC) USING BTREE,
  INDEX `idx_aba_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_aba_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = 'A/B 实验顾客分桶表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for ab_experiments
-- ----------------------------
CREATE TABLE IF NOT EXISTS `ab_experiments` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `experiment_key` varchar(64) NOT NULL COMMENT '实验唯一标识，如 reco_algo',
  `name` varchar(100) NOT NULL COMMENT '实验名称',
  `status` varchar(10) NOT NULL DEFAULT 'DRAFT' COMMENT '状态：DRAFT/RUNNING/PAUSED',
  `traffic_percent` int NOT NULL DEFAULT 0 COMMENT '参与实验流量百分比 0-100；其余走默认桶',
  `default_bucket` varchar(40) NOT NULL DEFAULT 'v1' COMMENT '未命中流量的默认桶编码',
  `buckets` json NULL COMMENT '桶定义，如 [{\"code\":\"v1\",\"traffic\":50,\"params\":{}}]',
  `description` varchar(255) NULL DEFAULT NULL COMMENT '实验说明',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_abe_key`(`experiment_key` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = 'A/B 实验定义表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for balance_ledger
-- ----------------------------
CREATE TABLE IF NOT EXISTS `balance_ledger` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '发生门店 → stores.id',
  `order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联餐饮订单 → orders.id',
  `recharge_order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联充值单 → recharge_orders.id',
  `payment_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联支付单 → payments.id',
  `refund_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联退款单 → payment_refunds.id',
  `operator_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '操作员工 → users.id（人工调账等）',
  `biz_no` varchar(40) NULL DEFAULT NULL COMMENT '业务关联单号（如调账单号 WA…；充值/消费亦可冗余）',
  `entry_type` varchar(30) NOT NULL COMMENT 'TOPUP=充值入账 | PAY=余额消费 | REFUND_RESTORE=退款回补 | ADJUST=人工调账 | MERGE=合并',
  `delta_principal` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '本金变动（可负）',
  `delta_gift` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '赠送金变动（可负）',
  `principal_after` decimal(10, 2) NOT NULL COMMENT '变动后本金余额',
  `gift_after` decimal(10, 2) NOT NULL COMMENT '变动后赠送金余额',
  `remark` varchar(255) NULL DEFAULT NULL COMMENT '备注',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '记录时间',
  `pay_order_id` bigint UNSIGNED GENERATED ALWAYS AS (
    if(
      (`entry_type` = _utf8mb4 'PAY'),
      `order_id`,
      NULL
    )
  ) STORED COMMENT 'PAY 幂等：每单至多一条扣款' NULL,
  `topup_recharge_id` bigint UNSIGNED GENERATED ALWAYS AS (
    if(
      (`entry_type` = _utf8mb4 'TOPUP'),
      `recharge_order_id`,
      NULL
    )
  ) STORED COMMENT 'TOPUP 幂等：每充值单至多一条' NULL,
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_bl_pay_order`(`pay_order_id` ASC) USING BTREE,
  UNIQUE INDEX `uk_bl_topup_recharge`(`topup_recharge_id` ASC) USING BTREE,
  UNIQUE INDEX `uk_bl_refund`(`refund_id` ASC) USING BTREE,
  UNIQUE INDEX `uk_bl_biz_no`(`biz_no` ASC) USING BTREE,
  INDEX `idx_bl_customer_created`(`customer_id` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_bl_store_created`(`store_id` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_bl_payment`(`payment_id` ASC) USING BTREE,
  INDEX `idx_bl_operator`(`operator_id` ASC) USING BTREE,
  CONSTRAINT `fk_bl_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_bl_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_bl_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_bl_operator` FOREIGN KEY (`operator_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '会员储值流水（本金/赠送金）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for cameras
-- ----------------------------
CREATE TABLE IF NOT EXISTS `cameras` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '所属门店 → stores.id',
  `name` varchar(100) NOT NULL COMMENT '摄像头名称/位置',
  `camera_type` varchar(20) NOT NULL COMMENT 'USB=本机摄像头 | RTSP=网络流 | HTTP=HTTP 流',
  `source_url` varchar(500) NOT NULL COMMENT '设备号或 RTSP/HTTP 地址',
  `username` varchar(100) NULL DEFAULT NULL COMMENT '拉流用户名',
  `password_encrypted` varchar(500) NULL DEFAULT NULL COMMENT '加密后的拉流密码',
  `status` varchar(20) NOT NULL DEFAULT 'OFFLINE' COMMENT 'ONLINE/OFFLINE/RECONNECTING',
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
-- Table structure for cashier_shifts
-- ----------------------------
CREATE TABLE IF NOT EXISTS `cashier_shifts` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `operator_id` bigint UNSIGNED NOT NULL COMMENT '收银员 → users.id',
  `opening_float` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '开班备用金（元）',
  `status` varchar(20) NOT NULL DEFAULT 'OPEN' COMMENT 'OPEN=营业中 CLOSED=已交班',
  `opened_at` datetime(3) NOT NULL COMMENT '开班时间',
  `closed_at` datetime(3) NULL DEFAULT NULL COMMENT '交班时间',
  `total_cash` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '本班现金收款',
  `total_wechat` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '本班微信收款',
  `total_alipay` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '本班支付宝收款',
  `total_amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '本班收款合计',
  `closing_cash` decimal(10, 2) NULL DEFAULT NULL COMMENT '交班实盘现金（含备用金）',
  `cash_difference` decimal(10, 2) NULL DEFAULT NULL COMMENT '现金长短款',
  `closed_by` bigint UNSIGNED NULL DEFAULT NULL COMMENT '确认日结的管理员 → users.id',
  `remark` varchar(500) NULL DEFAULT NULL COMMENT '交班备注',
  `open_operator_key` bigint UNSIGNED GENERATED ALWAYS AS (
    if(
      (`status` = _utf8mb4 'OPEN'),
      `operator_id`,
      NULL
    )
  ) STORED COMMENT 'OPEN 时为 operator_id，CLOSED 为 NULL；用于唯一约束' NULL,
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_cashier_shift_one_open`(`open_operator_key` ASC) USING BTREE,
  INDEX `idx_shift_store_open`(`store_id` ASC, `opened_at` ASC) USING BTREE,
  INDEX `idx_shift_operator_status`(`operator_id` ASC, `status` ASC) USING BTREE,
  CONSTRAINT `fk_shift_operator` FOREIGN KEY (`operator_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_shift_closed_by` FOREIGN KEY (`closed_by`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_shift_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '收银班次表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for category_sort_schedule_items
-- ----------------------------
CREATE TABLE IF NOT EXISTS `category_sort_schedule_items` (
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
-- Table structure for category_sort_schedules
-- ----------------------------
CREATE TABLE IF NOT EXISTS `category_sort_schedules` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `name` varchar(100) NOT NULL COMMENT '方案名（如 早餐优先）',
  `start_time` time NOT NULL COMMENT '开始时间（含）',
  `end_time` time NOT NULL COMMENT '结束时间（不含）；若 start>end 表示跨午夜',
  `priority` int NOT NULL DEFAULT 0 COMMENT '重叠时取更大优先级',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  `auto_sort` tinyint NOT NULL DEFAULT 0 COMMENT '1=按该时段销量自动刷新分类排序；0=仅手动',
  `auto_updated_at` datetime(3) NULL DEFAULT NULL COMMENT '最近一次按销量自动刷新时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_cat_sched_store`(`store_id` ASC, `status` ASC, `priority` ASC) USING BTREE,
  CONSTRAINT `fk_cat_sched_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '分类分时段排序方案' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for coupon_templates
-- ----------------------------
CREATE TABLE IF NOT EXISTS `coupon_templates` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT 'NULL=总部全店模板；非空=仅该门店 → stores.id',
  `name` varchar(100) NOT NULL COMMENT '券名称（展示用）',
  `coupon_type` varchar(20) NOT NULL DEFAULT 'FIXED' COMMENT 'FIXED=满减元 | PERCENT=折扣比例 | VOUCHER=代金券抵扣',
  `discount_value` decimal(10, 2) NOT NULL COMMENT 'FIXED/VOUCHER=减免金额（元，两位）；PERCENT=折扣率两位，如0.90表示九折',
  `min_order_amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '使用门槛（按会员折后餐品小计计）',
  `valid_days` int NOT NULL DEFAULT 30 COMMENT '领取/发放后有效天数',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '模板状态：1=启用 0=停用',
  `total_quota` int NULL DEFAULT NULL COMMENT 'P0-6: 发放总量上限；NULL=不限',
  `remaining` int NULL DEFAULT NULL COMMENT 'P0-6: 剩余可发量；NULL=不限',
  `store_whitelist` json NULL COMMENT 'P2-4: 总部模板可见门店白名单；NULL/空数组=全部门店',
  `schedule_mode` varchar(20) NOT NULL DEFAULT 'ALWAYS' COMMENT 'ALWAYS=全年可发；ANNUAL_MD=仅每年月日窗口内可发',
  `schedule_calendar` varchar(10) NOT NULL DEFAULT 'SOLAR' COMMENT 'SOLAR=公历 LUNAR=农历（仅 ANNUAL_MD）',
  `annual_start_month` tinyint UNSIGNED NULL DEFAULT NULL COMMENT '发放窗起月 1-12',
  `annual_start_day` tinyint UNSIGNED NULL DEFAULT NULL COMMENT '发放窗起日 1-31',
  `annual_end_month` tinyint UNSIGNED NULL DEFAULT NULL COMMENT '发放窗止月；空=仅起日当天',
  `annual_end_day` tinyint UNSIGNED NULL DEFAULT NULL COMMENT '发放窗止日',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_coupon_tmpl_store`(`store_id` ASC) USING BTREE,
  INDEX `idx_coupon_tmpl_schedule`(`schedule_mode` ASC, `status` ASC) USING BTREE,
  CONSTRAINT `fk_coupon_tmpl_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '优惠券/代金券模板表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for order_milestone_rewards
-- ----------------------------
CREATE TABLE IF NOT EXISTS `order_milestone_rewards` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id（按本店累计成功支付单数）',
  `threshold_orders` int NOT NULL COMMENT '累计成功支付订单数阈值（如 20/50/100）',
  `template_id` bigint UNSIGNED NOT NULL COMMENT '发放的券模板 → coupon_templates.id',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_omr_store_threshold`(`store_id` ASC, `threshold_orders` ASC) USING BTREE,
  INDEX `idx_omr_store_status`(`store_id` ASC, `status` ASC) USING BTREE,
  INDEX `idx_omr_template`(`template_id` ASC) USING BTREE,
  CONSTRAINT `fk_omr_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_omr_template` FOREIGN KEY (`template_id`) REFERENCES `coupon_templates` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '门店累计成单里程碑发券规则' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for customer_alipay_accounts
-- ----------------------------
CREATE TABLE IF NOT EXISTS `customer_alipay_accounts` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `alipay_user_id` varchar(64) NOT NULL COMMENT '支付宝 user_id',
  `alipay_union_id` varchar(64) NULL DEFAULT NULL COMMENT '支付宝 union_id（如有）',
  `profile_completed` tinyint NOT NULL DEFAULT 0 COMMENT '本渠道是否完善过昵称/头像资料（≠member_status）',
  `nickname` varchar(100) NULL DEFAULT NULL COMMENT '本渠道展示昵称（与微信互不覆盖）',
  `avatar_url` varchar(500) NULL DEFAULT NULL COMMENT '本渠道展示头像（与微信互不覆盖）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '首次绑定时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `alipay_user_id`(`alipay_user_id` ASC) USING BTREE,
  INDEX `fk_caa_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_caa_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '支付宝账号绑定表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for customer_behavior_events
-- ----------------------------
CREATE TABLE IF NOT EXISTS `customer_behavior_events` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `event_id` varchar(64) NOT NULL COMMENT '客户端生成的幂等 UUID，重复上报靠它去重',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '发生门店 → stores.id',
  `event_type` varchar(20) NOT NULL COMMENT 'EXPOSE/CLICK/CART_ADD/CART_REMOVE/ORDER_PAID/REORDER/FEEDBACK',
  `scene` varchar(20) NULL DEFAULT NULL COMMENT '场景：menu/recommend/cart/detail/settle/orders',
  `product_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联餐品 → products.id',
  `category_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联分类 → product_categories.id',
  `ab_bucket` varchar(80) NULL DEFAULT NULL COMMENT '命中的 A/B 桶，如 reco_algo:v2',
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
-- Table structure for customer_coupons
-- ----------------------------
CREATE TABLE IF NOT EXISTS `customer_coupons` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '持有人 → customers.id',
  `template_id` bigint UNSIGNED NOT NULL COMMENT '券模板 → coupon_templates.id',
  `coupon_no` varchar(40) NOT NULL COMMENT '券实例编号（全局唯一）',
  `status` varchar(20) NOT NULL DEFAULT 'AVAILABLE' COMMENT 'AVAILABLE=可用 | RESERVED=下单预占 | USED=已使用 | EXPIRED=已过期',
  `status_reason` varchar(40) NULL DEFAULT NULL COMMENT '状态原因：merge_dup=合并冲突作废 等',
  `obtained_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '领取/发放时间',
  `expires_at` datetime(3) NOT NULL COMMENT '过期时间',
  `used_order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '核销订单 → orders.id',
  `used_at` datetime(3) NULL DEFAULT NULL COMMENT '核销时间',
  `client_request_id` varchar(128) NULL DEFAULT NULL COMMENT 'P0-6: 客户端幂等键；同键重复发券只成功一次',
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
-- Table structure for customer_identities
-- ----------------------------
CREATE TABLE IF NOT EXISTS `customer_identities` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `provider` varchar(32) NOT NULL COMMENT 'WECHAT_OPENID/WECHAT_UNIONID/ALIPAY_USER_ID/PHONE',
  `provider_subject` varchar(128) NOT NULL COMMENT '渠道稳定身份；日志中必须脱敏',
  `verified` tinyint NOT NULL DEFAULT 1 COMMENT '是否经渠道服务端验证',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_customer_identity_provider_subject`(`provider` ASC, `provider_subject` ASC) USING BTREE,
  INDEX `idx_customer_identity_customer`(`customer_id` ASC, `provider` ASC) USING BTREE,
  CONSTRAINT `fk_customer_identity_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '统一顾客稳定身份绑定' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for customer_preferences
-- ----------------------------
CREATE TABLE IF NOT EXISTS `customer_preferences` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `category` varchar(50) NOT NULL COMMENT '规格组名称快照（如 辣度、份量）',
  `option_code` varchar(50) NOT NULL COMMENT '选项编码快照',
  `option_name` varchar(50) NULL DEFAULT NULL COMMENT '选项名称快照（展示用）',
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
  INDEX `idx_customer_prefs_score`(`customer_id` ASC, `preference_score` DESC) USING BTREE,
  CONSTRAINT `fk_pref_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '顾客口味偏好表（全门店汇总，不按店隔离）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for customer_taste_profiles
-- ----------------------------
CREATE TABLE IF NOT EXISTS `customer_taste_profiles` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `dim_type` varchar(16) NOT NULL COMMENT '维度类型：CATEGORY/PRODUCT/TASTE_TAG',
  `target_key` varchar(100) NOT NULL COMMENT '目标键：分类/餐品 id 字符串或标签字面量',
  `target_name` varchar(100) NULL DEFAULT NULL COMMENT '目标名称快照（展示用）',
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
  CONSTRAINT `fk_ctp_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '顾客多维口味画像表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for customer_wechat_accounts
-- ----------------------------
CREATE TABLE IF NOT EXISTS `customer_wechat_accounts` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `openid` varchar(64) NOT NULL COMMENT '微信 openid，小程序内唯一',
  `unionid` varchar(64) NULL DEFAULT NULL COMMENT '微信 unionid（同主体多应用打通）',
  `profile_completed` tinyint NOT NULL DEFAULT 0 COMMENT '本渠道是否完善过昵称/头像资料（≠member_status）',
  `nickname` varchar(100) NULL DEFAULT NULL COMMENT '本渠道展示昵称（与支付宝互不覆盖）',
  `avatar_url` varchar(500) NULL DEFAULT NULL COMMENT '本渠道展示头像（与支付宝互不覆盖）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '首次绑定时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `openid`(`openid` ASC) USING BTREE,
  INDEX `idx_wechat_unionid`(`unionid` ASC) USING BTREE,
  INDEX `fk_cwa_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_cwa_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '微信账号绑定表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for customers
-- ----------------------------
CREATE TABLE IF NOT EXISTS `customers` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_no` varchar(40) NOT NULL COMMENT '顾客编号（业务可读单号）',
  `display_name` varchar(100) NULL DEFAULT NULL COMMENT '展示名（可与昵称相同）',
  `nickname` varchar(100) NULL DEFAULT NULL COMMENT '收银台/后台展示昵称（小程序微信/支付宝各存在对应账号表）',
  `avatar_url` varchar(500) NULL DEFAULT NULL COMMENT '收银台/后台展示头像（小程序按渠道账号分存）',
  `phone` varchar(11) NULL DEFAULT NULL COMMENT '手机号；绑定后可合并历史账号',
  `member_level_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '当前会员等级 → member_levels.id',
  `points` int NOT NULL DEFAULT 0 COMMENT '累计积分；实付1元=1分，达门槛自动升级',
  `balance_principal` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '储值本金余额（元，顾客实付充入）',
  `balance_gift` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '储值赠送金余额（元，活动赠送；优先抵扣）',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '顾客状态：1=正常 0=禁用（兼容字段；以 account_status 为准）',
  `member_status` varchar(20) NOT NULL DEFAULT 'GUEST' COMMENT 'GUEST=游客（可点餐） MEMBER=已主动开通会员',
  `account_status` varchar(20) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/DISABLED/DELETED',
  `profile_status` varchar(20) NOT NULL DEFAULT 'EMPTY' COMMENT 'EMPTY/PARTIAL/COMPLETED 资料完善度（≠会员）',
  `birthday` date NULL DEFAULT NULL COMMENT '生日（会员生日权益），存公历 yyyy-mm-dd；农历模式下输入为农历月日再换算落库',
  `birthday_calendar` varchar(10) NOT NULL DEFAULT 'SOLAR' COMMENT '生日历法：SOLAR=公历 LUNAR=农历（按农历月日匹配权益日）',
  `birthday_locked` tinyint NOT NULL DEFAULT 0 COMMENT '生日是否已确认锁定：1=小程序不可再改（历法/日期）；0=未锁定',
  `phone_verified` tinyint NOT NULL DEFAULT 0 COMMENT '手机号是否已验证',
  `member_activated_at` datetime(3) NULL DEFAULT NULL COMMENT '首次主动开通会员时间（≠首次扫码）',
  `first_seen_at` datetime(3) NULL DEFAULT NULL COMMENT '首次扫码/识别时间（≠会员激活）',
  `first_seen_store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '首次扫码门店 → stores.id',
  `first_seen_table_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '首次扫码桌台 → tables.id',
  `token_version` int NOT NULL DEFAULT 0 COMMENT 'JWT 版本号；禁用/合并账号时递增，使旧令牌失效',
  `registered_store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '会员激活时门店 → stores.id；只记第一次',
  `registered_table_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '会员激活时桌台 → tables.id；只记第一次',
  `last_order_at` datetime(3) NULL DEFAULT NULL COMMENT '最近下单时间',
  `recommend_opt_in` int NOT NULL DEFAULT 0 COMMENT '个性化推荐开关：默认关闭；顾客明确同意后开启',
  `consent_version` varchar(40) NULL DEFAULT NULL COMMENT '个性化推荐同意的隐私政策版本（版本化留痕，PIPL 合规）',
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
-- Table structure for delivery_callback_logs
-- ----------------------------
CREATE TABLE IF NOT EXISTS `delivery_callback_logs` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `platform` varchar(20) NOT NULL COMMENT 'MEITUAN | ELEME',
  `event_type` varchar(50) NOT NULL COMMENT '回调事件类型',
  `event_id` varchar(128) NULL DEFAULT NULL COMMENT '平台事件 ID（幂等去重）',
  `platform_order_id` varchar(64) NULL DEFAULT NULL COMMENT '平台订单号',
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
-- Table structure for delivery_orders
-- ----------------------------
CREATE TABLE IF NOT EXISTS `delivery_orders` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '统一订单 → orders.id（一对一）',
  `platform` varchar(20) NOT NULL COMMENT 'MEITUAN | ELEME',
  `platform_order_id` varchar(64) NOT NULL COMMENT '平台订单号',
  `platform_day_seq` varchar(32) NULL DEFAULT NULL COMMENT '平台当日流水号/取餐号',
  `delivery_status` varchar(30) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/ACCEPTED/READY/DELIVERING/DONE/CANCELLED',
  `recipient_name` varchar(50) NULL DEFAULT NULL COMMENT '收件人姓名',
  `recipient_phone` varchar(20) NULL DEFAULT NULL COMMENT '收件人电话（可能是隐私号）',
  `recipient_address` varchar(500) NULL DEFAULT NULL COMMENT '配送地址',
  `rider_name` varchar(50) NULL DEFAULT NULL COMMENT '骑手姓名',
  `rider_phone` varchar(20) NULL DEFAULT NULL COMMENT '骑手电话',
  `expected_delivery_at` datetime(3) NULL DEFAULT NULL COMMENT '预计送达时间',
  `platform_fee` decimal(10, 2) NULL DEFAULT 0.00 COMMENT '平台服务费',
  `packing_fee` decimal(10, 2) NULL DEFAULT 0.00 COMMENT '打包费（平台侧）',
  `delivery_fee` decimal(10, 2) NULL DEFAULT 0.00 COMMENT '配送费',
  `logistics_status` varchar(30) NULL DEFAULT NULL COMMENT '物流配送状态',
  `logistics_code` varchar(50) NULL DEFAULT NULL COMMENT '物流状态码',
  `refund_status` varchar(30) NULL DEFAULT 'NONE' COMMENT 'NONE/PENDING/APPROVED/REJECTED',
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
-- Table structure for delivery_product_maps
-- ----------------------------
CREATE TABLE IF NOT EXISTS `delivery_product_maps` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `platform` varchar(20) NOT NULL COMMENT 'MEITUAN=美团 | ELEME=饿了么',
  `platform_sku_id` varchar(64) NOT NULL COMMENT '平台 SKU ID',
  `platform_spu_code` varchar(64) NULL DEFAULT NULL COMMENT '平台 SPU / app_food_code',
  `app_food_code` varchar(64) NULL DEFAULT NULL COMMENT '本系统对外餐品编码',
  `option_signature` varchar(128) NULL DEFAULT NULL COMMENT '规格组合签名（区分同 SKU 不同加料）',
  `platform_sku_name` varchar(100) NULL DEFAULT NULL COMMENT '平台侧 SKU 名称',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '映射的本系统餐品 → products.id',
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
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '外卖平台餐品映射表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for delivery_refunds
-- ----------------------------
CREATE TABLE IF NOT EXISTS `delivery_refunds` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `delivery_order_id` bigint UNSIGNED NOT NULL COMMENT '外卖扩展单 → delivery_orders.id',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '统一订单 → orders.id',
  `platform` varchar(20) NOT NULL COMMENT 'MEITUAN | ELEME',
  `platform_refund_id` varchar(64) NULL DEFAULT NULL COMMENT '平台退款单号',
  `event_fingerprint` varchar(64) NULL DEFAULT NULL COMMENT '无平台退款编号时的规范化事件指纹',
  `refund_type` varchar(30) NOT NULL DEFAULT 'FULL' COMMENT 'FULL=全额 | PARTIAL=部分',
  `amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '退款金额',
  `reason` varchar(500) NULL DEFAULT NULL COMMENT '退款原因',
  `status` varchar(30) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PROCESSING/APPROVED/REJECTED',
  `raw_payload` json NULL COMMENT '平台退款报文',
  `handled_at` datetime(3) NULL DEFAULT NULL COMMENT '处理完成时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_dr_platform_refund`(`platform` ASC, `platform_refund_id` ASC) USING BTREE,
  UNIQUE INDEX `uq_delivery_refund_event_fingerprint`(`platform` ASC, `event_fingerprint` ASC) USING BTREE,
  INDEX `idx_dr_order`(`order_id` ASC) USING BTREE,
  INDEX `fk_dr_do`(`delivery_order_id` ASC) USING BTREE,
  CONSTRAINT `fk_dr_do` FOREIGN KEY (`delivery_order_id`) REFERENCES `delivery_orders` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_dr_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '外卖退款单表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for delivery_sync_alerts
-- ----------------------------
CREATE TABLE IF NOT EXISTS `delivery_sync_alerts` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联门店 stores.id（可空）',
  `platform` varchar(20) NULL DEFAULT NULL COMMENT 'MEITUAN | ELEME',
  `alert_type` varchar(30) NOT NULL DEFAULT 'SYNC_FAIL' COMMENT 'REJECT_ORDER=库存拒单 | SYNC_FAIL=同步失败',
  `title` varchar(200) NOT NULL COMMENT '告警标题',
  `detail` text NULL COMMENT '告警详情/原因',
  `platform_order_id` varchar(64) NULL DEFAULT NULL COMMENT '关联平台订单号',
  `resolved` tinyint NOT NULL DEFAULT 0 COMMENT '0=未处理 1=已处理',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '产生时间',
  `resolved_at` datetime(3) NULL DEFAULT NULL COMMENT '处理时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_dsa_store_resolved`(`store_id` ASC, `resolved` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_dsa_platform_order`(`platform_order_id` ASC) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '外卖同步/拒单告警' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for dining_sessions
-- ----------------------------
CREATE TABLE IF NOT EXISTS `dining_sessions` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `table_id` bigint UNSIGNED NOT NULL COMMENT '桌台 → tables.id',
  `table_token` varchar(64) NULL DEFAULT NULL COMMENT '绑定时桌台二维码 token 快照',
  `status` varchar(20) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/EXPIRED/COMPLETED/CANCELLED',
  `started_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '会话开始',
  `last_active_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '最近活跃',
  `expires_at` datetime(3) NOT NULL COMMENT '过期时间',
  `ended_at` datetime(3) NULL DEFAULT NULL COMMENT '结束时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
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
-- Table structure for table_parties
-- ----------------------------
CREATE TABLE IF NOT EXISTS `table_parties` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `table_id` bigint UNSIGNED NOT NULL COMMENT '桌台 → tables.id',
  `host_customer_id` bigint UNSIGNED NOT NULL COMMENT '桌主（先扫码加入者）→ customers.id',
  `status` varchar(20) NOT NULL DEFAULT 'OPEN' COMMENT 'OPEN/CLOSED',
  `revision` int NOT NULL DEFAULT 1 COMMENT '共享车乐观锁版本，写操作自增',
  `expires_at` datetime(3) NOT NULL COMMENT '过期时间（超时未活动关闭）',
  `closed_at` datetime(3) NULL DEFAULT NULL COMMENT '关闭时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_tp_store_table_status`(`store_id` ASC, `table_id` ASC, `status` ASC) USING BTREE,
  INDEX `idx_tp_host`(`host_customer_id` ASC) USING BTREE,
  INDEX `fk_tp_table`(`table_id` ASC) USING BTREE,
  CONSTRAINT `fk_tp_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_tp_table` FOREIGN KEY (`table_id`) REFERENCES `tables` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_tp_host` FOREIGN KEY (`host_customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '同桌就餐局（共享车容器）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for table_party_members
-- ----------------------------
CREATE TABLE IF NOT EXISTS `table_party_members` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `party_id` bigint UNSIGNED NOT NULL COMMENT '就餐局 → table_parties.id',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `role` varchar(20) NOT NULL DEFAULT 'MEMBER' COMMENT 'HOST/MEMBER',
  `nickname_snap` varchar(100) NULL DEFAULT NULL COMMENT '加入时昵称快照',
  `joined_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '加入时间',
  `left_at` datetime(3) NULL DEFAULT NULL COMMENT '离席时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_tpm_party_customer`(`party_id` ASC, `customer_id` ASC) USING BTREE,
  INDEX `idx_tpm_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_tpm_party` FOREIGN KEY (`party_id`) REFERENCES `table_parties` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_tpm_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '同桌就餐局成员' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for shared_cart_items
-- ----------------------------
CREATE TABLE IF NOT EXISTS `shared_cart_items` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `party_id` bigint UNSIGNED NOT NULL COMMENT '就餐局 → table_parties.id',
  `added_by_customer_id` bigint UNSIGNED NOT NULL COMMENT '加菜人 → customers.id',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '餐品 → products.id',
  `product_name` varchar(100) NOT NULL COMMENT '加购时餐品名快照',
  `quantity` int NOT NULL DEFAULT 1 COMMENT '数量',
  `unit_price` decimal(10, 2) NOT NULL COMMENT '单价（含规格加价）',
  `option_ids` json NULL COMMENT '规格选项 id 列表',
  `option_text` varchar(255) NULL DEFAULT NULL COMMENT '规格文案',
  `remark` varchar(200) NULL DEFAULT NULL COMMENT '行备注',
  `category_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '分类 id 快照',
  `main_image` varchar(500) NULL DEFAULT NULL COMMENT '主图路径快照',
  `no_member_discount` tinyint NOT NULL DEFAULT 0 COMMENT '是否不计会员折',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_sci_party`(`party_id` ASC) USING BTREE,
  INDEX `idx_sci_adder`(`added_by_customer_id` ASC) USING BTREE,
  INDEX `idx_sci_product`(`product_id` ASC) USING BTREE,
  CONSTRAINT `fk_sci_party` FOREIGN KEY (`party_id`) REFERENCES `table_parties` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_sci_adder` FOREIGN KEY (`added_by_customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_sci_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '同桌共享购物车行（下单前草稿）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for face_profiles
-- ----------------------------
CREATE TABLE IF NOT EXISTS `face_profiles` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `feature_data` blob NOT NULL COMMENT '加密后的人脸特征向量',
  `feature_version` varchar(20) NOT NULL DEFAULT 'v1' COMMENT '特征提取算法版本',
  `consent_status` varchar(20) NOT NULL DEFAULT 'AUTHORIZED' COMMENT 'AUTHORIZED=已授权 | REVOKED=已撤销删除',
  `consent_at` datetime(3) NOT NULL COMMENT '授权采集时间',
  `revoked_at` datetime(3) NULL DEFAULT NULL COMMENT '撤销授权时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_face_customer`(`customer_id` ASC, `consent_status` ASC) USING BTREE,
  CONSTRAINT `fk_face_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '会员人脸特征档案表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for financial_ledger
-- ----------------------------
CREATE TABLE IF NOT EXISTS `financial_ledger` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `entry_type` varchar(20) NOT NULL COMMENT 'SALE=收款 | REFUND=退款 | ADJUSTMENT=调账',
  `channel` varchar(30) NULL DEFAULT NULL COMMENT '业务渠道：WECHAT/ALIPAY/POS/MEITUAN/ELEME 等',
  `provider` varchar(20) NULL DEFAULT NULL COMMENT '支付提供方：WECHAT/ALIPAY/CASH/MOCK',
  `amount` decimal(10, 2) NOT NULL COMMENT '金额（元；退款可为负或按 entry_type 区分）',
  `currency` varchar(8) NOT NULL DEFAULT 'CNY' COMMENT '币种',
  `order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联订单 → orders.id',
  `payment_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联支付单 → payments.id',
  `refund_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联退款单 → payment_refunds.id',
  `delivery_order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联外卖扩展单 → delivery_orders.id',
  `delivery_refund_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联外卖退款 → delivery_refunds.id',
  `remark` varchar(500) NULL DEFAULT NULL COMMENT '备注',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入账时间',
  PRIMARY KEY (`id`) USING BTREE,
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
  CONSTRAINT `fk_fl_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_fl_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_fl_payment` FOREIGN KEY (`payment_id`) REFERENCES `payments` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_fl_refund` FOREIGN KEY (`refund_id`) REFERENCES `payment_refunds` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_fl_delivery_order` FOREIGN KEY (`delivery_order_id`) REFERENCES `delivery_orders` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_fl_delivery_refund` FOREIGN KEY (`delivery_refund_id`) REFERENCES `delivery_refunds` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '财务分录账本' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for member_levels
-- ----------------------------
CREATE TABLE IF NOT EXISTS `member_levels` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `name` varchar(50) NOT NULL COMMENT '等级名称，如 普通会员、银卡会员',
  `min_points` int NOT NULL DEFAULT 0 COMMENT '升级所需累计积分下限',
  `discount_rate` decimal(5, 2) NOT NULL DEFAULT 1.00 COMMENT '折扣率，保留两位：1.00=无折扣，0.90=九折，0.98=9.8折',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '会员等级定义表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for operation_logs
-- ----------------------------
CREATE TABLE IF NOT EXISTS `operation_logs` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `user_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '操作人 → users.id；系统自动任务可为 NULL',
  `action` varchar(100) NOT NULL COMMENT '动作编码，如 order.cancel、product.update',
  `module` varchar(50) NOT NULL COMMENT '业务模块',
  `target_type` varchar(50) NULL DEFAULT NULL COMMENT '目标实体类型，如 Order、Product',
  `target_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '目标实体主键',
  `ip` varchar(45) NULL DEFAULT NULL COMMENT '客户端 IP（支持 IPv6）',
  `user_agent` varchar(500) NULL DEFAULT NULL COMMENT '浏览器/客户端 UA',
  `request_id` varchar(64) NULL DEFAULT NULL COMMENT '请求链路 ID，便于与日志系统关联',
  `detail` json NULL COMMENT '变更详情、请求参数摘要等 JSON',
  `category` varchar(20) NULL DEFAULT 'STAFF' COMMENT '审计分类：STAFF/STORE_ADMIN/CASHIER/KITCHEN/SYSTEM/CUSTOMER/DANGER/INTRUSION',
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
-- Table structure for option_groups
-- ----------------------------
CREATE TABLE IF NOT EXISTS `option_groups` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT 'NULL=总部共享；非空=仅该门店',
  `name` varchar(50) NOT NULL COMMENT '规格组显示名',
  `code` varchar(64) NOT NULL COMMENT '规格组编码：如 SH01-Liang（店内唯一，配合 store_scope）',
  `selection_type` varchar(20) NOT NULL DEFAULT 'SINGLE' COMMENT 'SINGLE=单选 | MULTIPLE=多选',
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
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '餐品规格组表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for options
-- ----------------------------
CREATE TABLE IF NOT EXISTS `options` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `group_id` bigint UNSIGNED NOT NULL COMMENT '规格组 → option_groups.id',
  `name` varchar(50) NOT NULL COMMENT '选项显示名，如 大份、微辣',
  `code` varchar(80) NOT NULL COMMENT '选项编码：如 SH01-Liang-XiaoWan（组内唯一）',
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
-- Table structure for order_item_options
-- ----------------------------
CREATE TABLE IF NOT EXISTS `order_item_options` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_item_id` bigint UNSIGNED NOT NULL COMMENT '订单明细 → order_items.id',
  `group_name_snapshot` varchar(50) NOT NULL COMMENT '规格组名称快照',
  `option_name_snapshot` varchar(50) NOT NULL COMMENT '选项名称快照',
  `option_code` varchar(50) NOT NULL COMMENT '选项编码快照（口味统计用）',
  `extra_price_snapshot` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '下单时该选项加价快照',
  `quantity` int NOT NULL DEFAULT 1 COMMENT '该选项数量（多选场景）',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_oio_item`(`order_item_id` ASC) USING BTREE,
  CONSTRAINT `fk_oio_item` FOREIGN KEY (`order_item_id`) REFERENCES `order_items` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '订单明细规格快照表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for order_items
-- ----------------------------
CREATE TABLE IF NOT EXISTS `order_items` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '餐品 → products.id（快照时有效 ID）',
  `product_name_snapshot` varchar(100) NOT NULL COMMENT '下单时餐品名称快照',
  `unit_price_snapshot` decimal(10, 2) NOT NULL COMMENT '下单时单价快照（含基础价，不含规格加价）',
  `quantity` int NOT NULL DEFAULT 1 COMMENT '购买数量',
  `subtotal` decimal(10, 2) NOT NULL COMMENT '行小计 = (单价+规格加价)×数量',
  `remark` varchar(255) NULL DEFAULT NULL COMMENT '单品备注（如 少葱）',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_oi_order`(`order_id` ASC) USING BTREE,
  INDEX `fk_oi_product`(`product_id` ASC) USING BTREE,
  CONSTRAINT `fk_oi_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_oi_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '订单餐品明细表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Records of order_items
-- ----------------------------
-- ----------------------------
-- Table structure for order_status_history
-- ----------------------------
DROP TABLE IF EXISTS `order_status_history`;
CREATE TABLE IF NOT EXISTS `order_status_history` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  `from_status` varchar(20) NULL DEFAULT NULL COMMENT '变更前状态',
  `to_status` varchar(20) NOT NULL COMMENT '变更后状态',
  `source` varchar(30) NOT NULL COMMENT '变更来源：STAFF=员工 | PAYMENT=支付 | SYSTEM=系统 | DELIVERY=外卖',
  `operator_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '操作员工 → users.id（如有）',
  `remark` varchar(255) NULL DEFAULT NULL COMMENT '变更说明',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '变更时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_osh_order`(`order_id` ASC, `created_at` ASC) USING BTREE,
  CONSTRAINT `fk_osh_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '订单状态变更历史表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for orders
-- ----------------------------
CREATE TABLE IF NOT EXISTS `orders` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_no` varchar(40) NOT NULL COMMENT '业务订单号（含门店/桌位/时间等可读信息）',
  `customer_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '下单顾客 → customers.id；匿名单可为空',
  `table_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '堂食桌台 → tables.id；外卖/打包可空',
  `dining_session_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '就餐会话 → dining_sessions.id',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING_PAYMENT' COMMENT '订单履约状态，见文件头枚举',
  `dining_type` varchar(20) NOT NULL DEFAULT 'DINE_IN' COMMENT 'DINE_IN=堂食 | TAKEAWAY=打包外带',
  `subtotal` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '餐品折前小计（含规格加价）',
  `discount_amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '会员等级折扣减免金额',
  `customer_coupon_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '使用的优惠券 → customer_coupons.id',
  `coupon_discount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '优惠券减免金额（非代金券）',
  `customer_voucher_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '使用的代金券 → customer_coupons.id',
  `voucher_discount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '代金券抵扣金额',
  `packing_fee` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '打包/餐盒费合计',
  `total_amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '应付总额（见文件头计算公式）',
  `payment_status` varchar(20) NOT NULL DEFAULT 'UNPAID' COMMENT 'UNPAID/PARTIAL_PAID/PAID/PARTIAL_REFUND/REFUNDING/REFUNDED',
  `payment_method` varchar(20) NULL DEFAULT NULL COMMENT '支付方式摘要：WECHAT/ALIPAY/CASH/BALANCE/MIXED',
  `channel` varchar(20) NULL DEFAULT NULL COMMENT '下单渠道：WECHAT/ALIPAY/MEITUAN/ELEME/H5',
  `remark` varchar(500) NULL DEFAULT NULL COMMENT '顾客备注',
  `idempotency_key` varchar(64) NULL DEFAULT NULL COMMENT '下单幂等键（按渠道/门店/操作员等作用域拼接，防重复提交；允许多 NULL）',
  `operator_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '下单/收银操作员 → users.id（POS 渠道必填）',
  `ordered_at` datetime(3) NULL DEFAULT NULL COMMENT '下单时间（业务时间）',
  `paid_at` datetime(3) NULL DEFAULT NULL COMMENT '支付成功时间',
  `completed_at` datetime(3) NULL DEFAULT NULL COMMENT '订单完成时间',
  `cancelled_at` datetime(3) NULL DEFAULT NULL COMMENT '取消时间',
  `kitchen_claimed_by` bigint UNSIGNED NULL DEFAULT NULL COMMENT '厨房认领人 → users.id；点「开始制作」写入',
  `kitchen_claimed_at` datetime(3) NULL DEFAULT NULL COMMENT '厨房认领时间',
  `scheduled_pickup_at` datetime(3) NULL DEFAULT NULL COMMENT '预点单预约取餐时间',
  `non_revenue` tinyint(1) NOT NULL DEFAULT 0 COMMENT '营销免单：不计普通营业收入',
  `marketing_campaign_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '来源营销活动',
  `flash_claim_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '来源抢购资格',
  `urge_count` int NOT NULL DEFAULT 0 COMMENT '顾客催单次数',
  `urged_at` datetime(3) NULL DEFAULT NULL COMMENT '最近一次催单时间',
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
  INDEX `ix_orders_marketing_campaign`(`marketing_campaign_id` ASC) USING BTREE,
  CONSTRAINT `fk_orders_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_orders_dining_session` FOREIGN KEY (`dining_session_id`) REFERENCES `dining_sessions` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_orders_coupon` FOREIGN KEY (`customer_coupon_id`) REFERENCES `customer_coupons` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_orders_voucher` FOREIGN KEY (`customer_voucher_id`) REFERENCES `customer_coupons` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_orders_kitchen_claim` FOREIGN KEY (`kitchen_claimed_by`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_orders_operator` FOREIGN KEY (`operator_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_orders_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_orders_table` FOREIGN KEY (`table_id`) REFERENCES `tables` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '订单主表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for outbox_events
-- ----------------------------
CREATE TABLE IF NOT EXISTS `outbox_events` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `aggregate_type` varchar(32) NOT NULL COMMENT '聚合类型：refund / paid_side_effect / ...',
  `aggregate_id` bigint UNSIGNED NOT NULL COMMENT '聚合 ID（refund_id / order_id / ...）',
  `event_kind` varchar(64) NOT NULL COMMENT '事件类型：refund_finalize / points_award / coupon_mark_used / pref_update / points_deduct_refund / pref_recompute',
  `payload` mediumtext NULL COMMENT 'JSON 负载（参数快照）',
  `sync_version` int NOT NULL DEFAULT 0 COMMENT '库存镜像同步版本',
  `status` varchar(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING / PROCESSING / DONE / FAILED',
  `attempt_count` int NOT NULL DEFAULT 0 COMMENT '消费尝试次数',
  `max_attempts` int NOT NULL DEFAULT 8 COMMENT '最大重试次数',
  `next_retry_at` datetime(3) NULL DEFAULT NULL COMMENT '下次允许重试时间',
  `locked_at` datetime(3) NULL DEFAULT NULL COMMENT '消费者加锁时间',
  `locked_by` varchar(64) NULL DEFAULT NULL COMMENT '锁持有者标识（hostname:pid）',
  `last_error` varchar(500) NULL DEFAULT NULL COMMENT '最近一次错误摘要',
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
-- Table structure for processed_messages
-- ----------------------------
CREATE TABLE IF NOT EXISTS `processed_messages` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `scope` varchar(64) NOT NULL COMMENT '业务域：payment_callback / print / outbox / webhook / ...',
  `message_id` varchar(128) NOT NULL COMMENT '业务唯一消息 ID（如 notify_id、print:order:ticket）',
  `meta_json` varchar(500) NULL DEFAULT NULL COMMENT '可选摘要（排障用，勿存敏感原文）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '首次处理时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_processed_msg`(`scope` ASC, `message_id` ASC) USING BTREE,
  INDEX `idx_processed_created`(`created_at` ASC) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '消息消费幂等表（MySQL 为事实源；Redis 仅加速）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for payment_callback_receipts
-- ----------------------------
CREATE TABLE IF NOT EXISTS `payment_callback_receipts` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `provider` varchar(20) NOT NULL COMMENT '支付渠道',
  `receipt_key` varchar(128) NOT NULL COMMENT '幂等键（如 notify_id + 交易号）',
  `payment_no` varchar(40) NULL DEFAULT NULL COMMENT '关联支付单号',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '首次收到时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_receipt`(`provider` ASC, `receipt_key` ASC) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '支付回调幂等回执表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for payment_refunds
-- ----------------------------
CREATE TABLE IF NOT EXISTS `payment_refunds` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `refund_no` varchar(40) NOT NULL COMMENT '退款单号',
  `payment_id` bigint UNSIGNED NOT NULL COMMENT '原支付单 → payments.id',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '关联订单 → orders.id',
  `amount` decimal(10, 2) NOT NULL COMMENT '退款金额',
  `reason` varchar(255) NULL DEFAULT NULL COMMENT '退款原因',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PROCESSING/SUCCESS/FAILED/CLOSED',
  `provider_refund_no` varchar(64) NULL DEFAULT NULL COMMENT '第三方退款单号',
  `callback_raw` json NULL COMMENT '退款回调/查询原文',
  `fail_reason` varchar(255) NULL DEFAULT NULL COMMENT '失败或关闭原因',
  `succeeded_at` datetime(3) NULL DEFAULT NULL COMMENT '退款成功确认时间',
  `operator_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '发起退款员工 → users.id',
  `retry_count` int NOT NULL DEFAULT 0 COMMENT '主动查询/重试次数',
  `next_retry_at` datetime(3) NULL DEFAULT NULL COMMENT '下次允许重试时间',
  `last_error` varchar(500) NULL DEFAULT NULL COMMENT '最近一次失败摘要',
  `idempotency_key` varchar(64) NULL DEFAULT NULL COMMENT '退款幂等键（防重复发起）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `refund_no`(`refund_no` ASC) USING BTREE,
  UNIQUE INDEX `uk_refund_idempotency`(`idempotency_key` ASC) USING BTREE,
  INDEX `idx_refunds_payment`(`payment_id` ASC) USING BTREE,
  INDEX `idx_refunds_operator`(`operator_id` ASC) USING BTREE,
  INDEX `idx_refunds_succeeded`(`status` ASC, `succeeded_at` ASC) USING BTREE,
  INDEX `idx_refunds_retry`(`status` ASC, `next_retry_at` ASC) USING BTREE,
  INDEX `fk_refund_order`(`order_id` ASC) USING BTREE,
  CONSTRAINT `fk_refund_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_refund_payment` FOREIGN KEY (`payment_id`) REFERENCES `payments` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_refund_operator` FOREIGN KEY (`operator_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '支付退款单表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for payment_status_history
-- ----------------------------
CREATE TABLE IF NOT EXISTS `payment_status_history` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `payment_id` bigint UNSIGNED NOT NULL COMMENT '支付单 → payments.id',
  `from_status` varchar(20) NULL DEFAULT NULL COMMENT '变更前状态',
  `to_status` varchar(20) NOT NULL COMMENT '变更后状态',
  `source` varchar(30) NOT NULL COMMENT 'CREATE/CALLBACK/REFUND/QUERY',
  `remark` varchar(255) NULL DEFAULT NULL COMMENT '备注',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '变更时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_psh_payment`(`payment_id` ASC, `created_at` ASC) USING BTREE,
  CONSTRAINT `fk_psh_payment` FOREIGN KEY (`payment_id`) REFERENCES `payments` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '支付状态变更历史表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for payments
-- ----------------------------
CREATE TABLE IF NOT EXISTS `payments` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `payment_no` varchar(40) NOT NULL COMMENT '支付单号',
  `order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联餐饮订单 → orders.id；储值充值可空',
  `recharge_order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联充值单 → recharge_orders.id；餐饮支付可空',
  `customer_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '付款顾客 → customers.id',
  `operator_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '收银操作员 → users.id',
  `provider` varchar(20) NOT NULL COMMENT '支付渠道：WECHAT | ALIPAY | CASH | BALANCE | MOCK',
  `channel` varchar(30) NOT NULL COMMENT '细分渠道，如 miniprogram、native、h5、BALANCE',
  `amount` decimal(10, 2) NOT NULL COMMENT '支付金额（元）',
  `currency` varchar(10) NOT NULL DEFAULT 'CNY' COMMENT '币种',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PAID/PARTIAL_REFUND/REFUNDED/CLOSED/FAILED',
  `provider_trade_no` varchar(64) NULL DEFAULT NULL COMMENT '第三方交易号',
  `provider_order_no` varchar(64) NULL DEFAULT NULL COMMENT '第三方商户订单号',
  `callback_raw` json NULL COMMENT '支付回调原文（审计）',
  `paid_at` datetime(3) NULL DEFAULT NULL COMMENT '支付成功时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `payment_no`(`payment_no` ASC) USING BTREE,
  UNIQUE INDEX `uk_provider_trade`(`provider` ASC, `provider_trade_no` ASC) USING BTREE,
  INDEX `idx_payments_order_status`(`order_id` ASC, `status` ASC) USING BTREE,
  INDEX `idx_payments_recharge`(`recharge_order_id` ASC, `status` ASC) USING BTREE,
  INDEX `idx_payments_created`(`created_at` ASC) USING BTREE,
  INDEX `idx_payments_paid_at`(`paid_at` ASC) USING BTREE,
  INDEX `idx_payments_status_paid`(`status` ASC, `paid_at` ASC) USING BTREE,
  INDEX `idx_payments_operator`(`operator_id` ASC, `paid_at` ASC) USING BTREE,
  INDEX `fk_payments_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_payments_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_payments_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_payments_recharge` FOREIGN KEY (`recharge_order_id`) REFERENCES `recharge_orders` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_payments_operator` FOREIGN KEY (`operator_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `chk_payments_target` CHECK (
    (
      `order_id` is not null
      and `recharge_order_id` is null
    )
    or (
      `order_id` is null
      and `recharge_order_id` is not null
    )
  )
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '支付单表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for permissions
-- ----------------------------
CREATE TABLE IF NOT EXISTS `permissions` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `code` varchar(100) NOT NULL COMMENT '权限码，如 product:read、order:update、welcome:manage',
  `name` varchar(100) NOT NULL COMMENT '权限名称（中文展示）',
  `module` varchar(50) NOT NULL COMMENT '所属模块：product/order/payment/customer 等',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `code`(`code` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 21 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '权限点表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for platform_category_maps
-- ----------------------------
CREATE TABLE IF NOT EXISTS `platform_category_maps` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '门店 → stores.id；NULL=总部默认映射',
  `platform` varchar(20) NOT NULL COMMENT 'MEITUAN | ELEME',
  `local_category_id` bigint UNSIGNED NOT NULL COMMENT '本地分类 → product_categories.id',
  `platform_category_code` varchar(64) NULL DEFAULT NULL COMMENT '平台侧分类编码',
  `platform_category_name` varchar(100) NULL DEFAULT NULL COMMENT '平台侧分类名称',
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
-- Table structure for platform_store_bindings
-- ----------------------------
CREATE TABLE IF NOT EXISTS `platform_store_bindings` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '本系统门店 → stores.id',
  `platform` varchar(20) NOT NULL COMMENT 'MEITUAN | ELEME',
  `platform_shop_id` varchar(64) NOT NULL COMMENT '平台门店 ID',
  `platform_shop_name` varchar(100) NULL DEFAULT NULL COMMENT '平台门店名称',
  `access_token` text NULL COMMENT 'OAuth access_token',
  `refresh_token` text NULL COMMENT 'OAuth refresh_token',
  `token_expires_at` datetime(3) NULL DEFAULT NULL COMMENT 'token 过期时间',
  `extra_config` json NULL COMMENT '扩展配置（app_key、回调地址等）',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=已绑定 0=已解绑',
  `auto_sync_open` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否按营业时间自动开/关店（默认关闭，逐店开启）',
  `platform_open_status` varchar(20) NULL DEFAULT NULL COMMENT '最近同步到平台的营业状态 OPEN/REST/OFFLINE',
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
-- Table structure for point_ledger
-- ----------------------------
CREATE TABLE IF NOT EXISTS `point_ledger` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联订单 → orders.id',
  `refund_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联退款单 → payment_refunds.id',
  `delta` int NOT NULL COMMENT '积分变动（可正可负）',
  `balance_after` int NOT NULL COMMENT '变动后余额',
  `entry_type` varchar(30) NOT NULL COMMENT '变动类型：EARN/REFUND_DEDUCT/ADJUST 等',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '记录时间',
  `earn_order_id` bigint UNSIGNED GENERATED ALWAYS AS (
    if(
      (`entry_type` = _utf8mb4 'EARN'),
      `order_id`,
      NULL
    )
  ) STORED COMMENT 'EARN 幂等键：每单至多一条发放' NULL,
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_pl_refund`(`refund_id` ASC) USING BTREE,
  UNIQUE INDEX `uk_pl_earn_order`(`earn_order_id` ASC) USING BTREE,
  INDEX `idx_pl_customer_created`(`customer_id` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_pl_order`(`order_id` ASC) USING BTREE,
  CONSTRAINT `fk_pl_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_pl_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_pl_refund` FOREIGN KEY (`refund_id`) REFERENCES `payment_refunds` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '会员积分流水' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for pos_refund_requests
-- ----------------------------
CREATE TABLE IF NOT EXISTS `pos_refund_requests` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  `payment_id` bigint UNSIGNED NOT NULL COMMENT '主支付单（组合支付时为分摊首笔）→ payments.id',
  `amount` decimal(10, 2) NOT NULL COMMENT '申请退款金额（元）',
  `executed_amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '已成功执行退款合计（组合分摊）',
  `reason` varchar(255) NOT NULL COMMENT '申请原因',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PROCESSING/REJECTED/CANCELLED/SUCCESS/FAILED',
  `requested_by` bigint UNSIGNED NULL DEFAULT NULL COMMENT '申请人（收银员；顾客自助申请时为 NULL）→ users.id',
  `reviewed_by` bigint UNSIGNED NULL DEFAULT NULL COMMENT '审批人 → users.id',
  `reviewed_at` datetime(3) NULL DEFAULT NULL COMMENT '审批时间',
  `review_remark` varchar(500) NULL DEFAULT NULL COMMENT '审批备注（拒绝原因等）',
  `refund_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '执行后的退款单 → payment_refunds.id',
  `fail_reason` varchar(255) NULL DEFAULT NULL COMMENT '执行失败原因',
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
-- Table structure for print_jobs
-- ----------------------------
CREATE TABLE IF NOT EXISTS `print_jobs` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  `ticket_type` varchar(20) NOT NULL DEFAULT 'KITCHEN' COMMENT 'KITCHEN/URGE/CANCEL',
  `content_hash` varchar(64) NULL DEFAULT NULL COMMENT '内容 SHA-256',
  `content` text NULL COMMENT '小票内容（已清控制字符，可截断）',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PRINTING/SUCCESS/FAILED/RETRYING',
  `attempt_count` int NOT NULL DEFAULT 0 COMMENT '已尝试次数（含当前）',
  `next_retry_at` datetime(3) NULL DEFAULT NULL COMMENT '下次允许重试时间（指数退避）',
  `last_error` varchar(500) NULL DEFAULT NULL COMMENT '最近一次失败摘要',
  `locked_at` datetime(3) NULL DEFAULT NULL COMMENT '消费者加锁时间',
  `locked_by` varchar(64) NULL DEFAULT NULL COMMENT '锁持有者标识（实例/worker）',
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
-- Table structure for product_categories
-- ----------------------------
CREATE TABLE IF NOT EXISTS `product_categories` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '所属门店 → stores.id',
  `category_no` varchar(64) NOT NULL COMMENT '分类编号：{store_no}-拼音逻辑，如 SH01-Mian',
  `name` varchar(100) NOT NULL COMMENT '分类名称（如 招牌面、小吃）',
  `description` varchar(255) NULL DEFAULT NULL COMMENT '分类描述',
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
) ENGINE = InnoDB AUTO_INCREMENT = 6 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '餐品分类表（按门店）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for product_feedbacks
-- ----------------------------
CREATE TABLE IF NOT EXISTS `product_feedbacks` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '餐品 → products.id',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '评价发生门店 → stores.id',
  `verdicts` json NULL COMMENT '白名单标签，如 [\"TOO_SPICY\",\"DISLIKE\"] / [\"TASTY\"]',
  `sentiment` varchar(10) NOT NULL DEFAULT 'NEUTRAL' COMMENT '情感：POSITIVE/NEGATIVE/NEUTRAL',
  `remark` varchar(255) NULL DEFAULT NULL COMMENT '备注',
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
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '餐品轻量评价表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for product_images
-- ----------------------------
CREATE TABLE IF NOT EXISTS `product_images` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '餐品 → products.id',
  `image_url` varchar(500) NOT NULL COMMENT '图片 URL',
  `image_type` varchar(20) NOT NULL DEFAULT 'DETAIL' COMMENT 'MAIN=主图 | DETAIL=详情图 | OPTION=规格示意图',
  `sort_order` int NOT NULL DEFAULT 0 COMMENT '同类型内排序',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_pi_product`(`product_id` ASC, `sort_order` ASC) USING BTREE,
  CONSTRAINT `fk_pi_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '餐品图片表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for product_option_groups
-- ----------------------------
CREATE TABLE IF NOT EXISTS `product_option_groups` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '餐品 → products.id',
  `group_id` bigint UNSIGNED NOT NULL COMMENT '规格组 → option_groups.id',
  `sort_order` int NOT NULL DEFAULT 0 COMMENT '该餐品下规格组展示顺序',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_product_group`(`product_id` ASC, `group_id` ASC) USING BTREE,
  INDEX `fk_pog_group`(`group_id` ASC) USING BTREE,
  CONSTRAINT `fk_pog_group` FOREIGN KEY (`group_id`) REFERENCES `option_groups` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_pog_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 92 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '餐品-规格组关联表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for product_taste_profiles
-- ----------------------------
CREATE TABLE IF NOT EXISTS `product_taste_profiles` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '餐品 → products.id',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `dimensions` json NULL COMMENT '口味维度分，如 {\"spicy\":2,\"sweetness\":0,\"numbing\":0}',
  `tags` json NULL COMMENT '口味标签，如 [\"劲道\",\"酸辣\",\"牛肉\"]',
  `source` varchar(10) NOT NULL DEFAULT 'AUTO' COMMENT '来源：AUTO=自动推导 MANUAL=人工微调',
  `manual_overrides` json NULL COMMENT '人工接管的键；AUTO 重算时跳过',
  `derived_from` varchar(200) NULL DEFAULT NULL COMMENT '推导依据说明',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_ptp_product`(`product_id` ASC) USING BTREE,
  INDEX `idx_ptp_store`(`store_id` ASC) USING BTREE,
  CONSTRAINT `fk_ptp_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_ptp_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 15 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '产品口味画像表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for products
-- ----------------------------
CREATE TABLE IF NOT EXISTS `products` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '所属门店 → stores.id',
  `category_id` bigint UNSIGNED NOT NULL COMMENT '分类 → product_categories.id',
  `product_no` varchar(64) NOT NULL COMMENT '店内餐品编号：{store_no|0000}-分类逻辑-餐品逻辑，如 SH01-Mian-NiuRouMian',
  `name` varchar(100) NOT NULL COMMENT '餐品名称',
  `description` text NULL COMMENT '餐品描述/卖点',
  `main_image` varchar(500) NULL DEFAULT NULL COMMENT '主图相对或绝对路径',
  `price` decimal(10, 2) NOT NULL COMMENT '基础售价（不含规格加价）',
  `original_price` decimal(10, 2) NULL DEFAULT NULL COMMENT '划线原价（可选）',
  `cost_price` decimal(10, 2) NULL DEFAULT NULL COMMENT '成本价（字段级权限 product:cost:read；收银默认不可见）',
  `stock` int NOT NULL DEFAULT 999 COMMENT '默认库存；999 表示不限量',
  `box_price` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '单份餐盒/打包费',
  `min_order_count` int NOT NULL DEFAULT 1 COMMENT '起售份数',
  `require_category_ids` json NULL COMMENT '须先点过的分类 id（OR）；NULL=继承所属分类默认',
  `require_product_ids` json NULL COMMENT '须先点过的指定餐品 id（OR）；与分类依赖合并 OR',
  `status` varchar(20) NOT NULL DEFAULT 'ON_SALE' COMMENT 'ON_SALE=在售 | OFF_SALE=停售 | SOLD_OUT=售罄',
  `is_recommended` tinyint NOT NULL DEFAULT 0 COMMENT '店长推荐：1=是 0=否（仅人工勾选，夜间批不覆盖）',
  `is_hot` tinyint NOT NULL DEFAULT 0 COMMENT '热销标签：1=是 0=否（可夜间批按销量自动刷新）',
  `no_member_discount` tinyint NOT NULL DEFAULT 0 COMMENT '特价/不参与会员折扣：1=是 0=否',
  `no_cooking` tinyint NOT NULL DEFAULT 0 COMMENT '免烹饪/现成品（卤味等）：1=KDS可直出餐 0=需正常制作',
  `visibility_rule` varchar(20) NOT NULL DEFAULT 'ALWAYS' COMMENT '展示规则：ALWAYS=常显 DATE_RANGE=日期区间 ANNUAL_MD=每年月日 BIRTHDAY_TODAY=顾客今日生日',
  `visibility_calendar` varchar(10) NOT NULL DEFAULT 'SOLAR' COMMENT '展示历法：SOLAR=公历 LUNAR=农历（ANNUAL_MD按历法月日每年匹配；DATE_RANGE仍按落库公历绝对日期）',
  `visible_from` date NULL DEFAULT NULL COMMENT 'DATE_RANGE起/ANNUAL_MD起始月日（存公历锚点；ANNUAL_MD年忽略；农历时取该日对应农历月日）',
  `visible_to` date NULL DEFAULT NULL COMMENT 'DATE_RANGE止/ANNUAL_MD结束月日（空=仅起日当天）',
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
) ENGINE = InnoDB AUTO_INCREMENT = 15 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '餐品表（按门店）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for recharge_packages
-- ----------------------------
CREATE TABLE IF NOT EXISTS `recharge_packages` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT 'NULL=总部全店可用；非空=仅该门店',
  `name` varchar(100) NOT NULL COMMENT '档位名称，如 充100得120',
  `pay_amount` decimal(10, 2) NOT NULL COMMENT '顾客实付金额（元）',
  `gift_amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '赠送到账金额（元）',
  `sort_order` int NOT NULL DEFAULT 0 COMMENT '展示排序，越小越靠前',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=上架 0=下架',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  `scope_store_id` bigint UNSIGNED GENERATED ALWAYS AS (ifnull(`store_id`, 0)) STORED COMMENT '唯一约束用：总部 NULL 视为 0' NULL,
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_rp_scope_name`(`scope_store_id` ASC, `name` ASC) USING BTREE,
  UNIQUE INDEX `uk_rp_scope_pay`(`scope_store_id` ASC, `pay_amount` ASC) USING BTREE,
  INDEX `idx_rp_store_status`(`store_id` ASC, `status` ASC, `sort_order` ASC) USING BTREE,
  CONSTRAINT `fk_rp_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `chk_rp_pay_pos` CHECK (`pay_amount` > 0),
  CONSTRAINT `chk_rp_gift_nonneg` CHECK (`gift_amount` >= 0)
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '会员储值充值档位' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for recharge_orders
-- ----------------------------
CREATE TABLE IF NOT EXISTS `recharge_orders` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `recharge_no` varchar(40) NOT NULL COMMENT '充值单号',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '充值归属门店 → stores.id',
  `package_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '选用档位 → recharge_packages.id；自定义充值可空',
  `pay_amount` decimal(10, 2) NOT NULL COMMENT '实付金额（元）',
  `credit_principal` decimal(10, 2) NOT NULL COMMENT '入账本金（通常=pay_amount）',
  `credit_gift` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '入账赠送金',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PAID/CLOSED/FAILED',
  `channel` varchar(20) NULL DEFAULT NULL COMMENT '渠道：WECHAT/ALIPAY/POS',
  `payment_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '成功支付单 → payments.id',
  `paid_at` datetime(3) NULL DEFAULT NULL COMMENT '支付成功时间',
  `idempotency_key` varchar(64) NULL DEFAULT NULL COMMENT '创建幂等键',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_ro_no`(`recharge_no` ASC) USING BTREE,
  UNIQUE INDEX `uk_ro_idem`(`idempotency_key` ASC) USING BTREE,
  INDEX `idx_ro_customer`(`customer_id` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_ro_store_status`(`store_id` ASC, `status` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_ro_package`(`package_id` ASC) USING BTREE,
  CONSTRAINT `fk_ro_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_ro_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_ro_package` FOREIGN KEY (`package_id`) REFERENCES `recharge_packages` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '会员储值充值单' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for role_permissions
-- ----------------------------
CREATE TABLE IF NOT EXISTS `role_permissions` (
  `role_id` bigint UNSIGNED NOT NULL COMMENT '角色 ID → roles.id',
  `permission_id` bigint UNSIGNED NOT NULL COMMENT '权限 ID → permissions.id',
  PRIMARY KEY (`role_id`, `permission_id`) USING BTREE,
  INDEX `fk_rp_perm`(`permission_id` ASC) USING BTREE,
  CONSTRAINT `fk_rp_perm` FOREIGN KEY (`permission_id`) REFERENCES `permissions` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_rp_role` FOREIGN KEY (`role_id`) REFERENCES `roles` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '角色-权限关联表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for roles
-- ----------------------------
CREATE TABLE IF NOT EXISTS `roles` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `code` varchar(50) NOT NULL COMMENT '角色编码：SUPER_ADMIN=超管 | STORE_ADMIN=店长 | CASHIER=收银 | KITCHEN=后厨 | OPERATOR=运营',
  `name` varchar(100) NOT NULL COMMENT '角色显示名称',
  `description` varchar(255) NULL DEFAULT NULL COMMENT '角色职责说明',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `code`(`code` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 6 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '后台角色表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for store_business_hours
-- ----------------------------
CREATE TABLE IF NOT EXISTS `store_business_hours` (
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
  CONSTRAINT `fk_sbh_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '门店营业时间（多时段/跨午夜）' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for store_product_stocks
-- ----------------------------
CREATE TABLE IF NOT EXISTS `store_product_stocks` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '餐品 → products.id',
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
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '分店餐品库存覆盖表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for store_settings
-- ----------------------------
CREATE TABLE IF NOT EXISTS `store_settings` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `setting_key` varchar(100) NOT NULL COMMENT '参数键，如 packing_fee_enabled',
  `setting_value` varchar(500) NOT NULL COMMENT '参数值（字符串存储，由业务解析类型）',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '最后修改时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_store_setting`(`store_id` ASC, `setting_key` ASC) USING BTREE,
  CONSTRAINT `fk_ss_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '门店业务参数表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for stores
-- ----------------------------
CREATE TABLE IF NOT EXISTS `stores` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_no` varchar(32) NOT NULL COMMENT '门店编号（写入业务单号前缀，如 TH01）',
  `name` varchar(100) NOT NULL COMMENT '门店名称',
  `logo` varchar(500) NULL DEFAULT NULL COMMENT '门店 Logo（站内 /uploads/stores/...）',
  `address` varchar(255) NULL DEFAULT NULL COMMENT '门店地址',
  `phone` varchar(20) NULL DEFAULT NULL COMMENT '门店联系电话',
  `latitude` decimal(10, 7) NULL DEFAULT NULL COMMENT '纬度（天气口味加权）',
  `longitude` decimal(10, 7) NULL DEFAULT NULL COMMENT '经度（天气口味加权）',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '营业状态：1=营业中 0=停业/禁用',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `store_no`(`store_no` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 3 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '门店表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for system_settings
-- ----------------------------
CREATE TABLE IF NOT EXISTS `system_settings` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `setting_key` varchar(100) NOT NULL COMMENT '配置键',
  `setting_value` text NOT NULL COMMENT '配置值',
  `description` varchar(255) NULL DEFAULT NULL COMMENT '配置说明',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '最后修改时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `setting_key`(`setting_key` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 38 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '系统全局配置表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for tables
-- ----------------------------
CREATE TABLE IF NOT EXISTS `tables` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '所属门店 → stores.id',
  `table_no` varchar(20) NOT NULL COMMENT '桌号编码（店内唯一，如 01、A12）',
  `table_name` varchar(50) NOT NULL COMMENT '顾客端展示名（如 1号桌、包厢A）',
  `qr_token` varchar(64) NOT NULL COMMENT '扫码令牌；正式环境随机串，开发环境可用桌号作 token',
  `wechat_qr_url` varchar(500) NULL DEFAULT NULL COMMENT '微信小程序码落地路径或参数',
  `alipay_qr_url` varchar(500) NULL DEFAULT NULL COMMENT '支付宝小程序码落地路径或参数',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '桌台状态：1=启用 0=禁用',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `qr_token`(`qr_token` ASC) USING BTREE,
  UNIQUE INDEX `uk_store_table`(`store_id` ASC, `table_no` ASC) USING BTREE,
  CONSTRAINT `fk_tables_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 4 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '堂食桌台表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for users
-- ----------------------------
CREATE TABLE IF NOT EXISTS `users` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `username` varchar(50) NOT NULL COMMENT '登录用户名，全局唯一',
  `password_hash` varchar(255) NOT NULL COMMENT 'bcrypt 等算法哈希后的密码，明文不入库',
  `display_name` varchar(100) NOT NULL COMMENT '界面显示姓名',
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
  CONSTRAINT `fk_users_role` FOREIGN KEY (`role_id`) REFERENCES `roles` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_users_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 5 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '后台员工用户表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for visitor_events
-- ----------------------------
CREATE TABLE IF NOT EXISTS `visitor_events` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `visitor_no` varchar(64) NOT NULL COMMENT '访客编号；会员事件可复用 customer_no',
  `camera_id` bigint UNSIGNED NOT NULL COMMENT '摄像头 → cameras.id',
  `customer_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '命中授权会员时 → customers.id；陌生客为空',
  `event_type` varchar(30) NOT NULL COMMENT 'ENTER=进入 | MATCH=会员识别 | EXIT=离开 | PASS=路过',
  `event_time` datetime(3) NOT NULL COMMENT '事件发生时间',
  `snapshot_path` varchar(500) NULL DEFAULT NULL COMMENT '抓拍图存储路径',
  `expires_at` datetime(3) NOT NULL COMMENT '数据保留到期时间（到期可清理）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入库时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_ve_camera_time`(`camera_id` ASC, `event_time` ASC) USING BTREE,
  INDEX `idx_ve_expires`(`expires_at` ASC) USING BTREE,
  INDEX `idx_ve_customer_time`(`customer_id` ASC, `event_time` ASC) USING BTREE,
  CONSTRAINT `fk_ve_camera` FOREIGN KEY (`camera_id`) REFERENCES `cameras` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_ve_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '客流/迎宾抓拍事件表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for webhook_inbox
-- ----------------------------
CREATE TABLE IF NOT EXISTS `webhook_inbox` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `channel` varchar(30) NOT NULL COMMENT 'WECHAT_PAY/ALIPAY/MEITUAN/ELEME',
  `event_id` varchar(128) NOT NULL COMMENT '平台事件唯一 ID（幂等）',
  `event_type` varchar(50) NULL DEFAULT NULL COMMENT '事件类型',
  `payload` mediumtext NOT NULL COMMENT '原始报文',
  `headers_json` text NULL COMMENT 'HTTP 头 JSON',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PROCESSING/DONE/FAILED',
  `error_message` varchar(500) NULL DEFAULT NULL COMMENT '处理失败原因',
  `attempt_count` int NOT NULL DEFAULT 0 COMMENT '消费尝试次数',
  `last_error` varchar(500) NULL DEFAULT NULL COMMENT '最近一次错误摘要',
  `next_retry_at` datetime(3) NULL DEFAULT NULL COMMENT '下次允许重试时间',
  `locked_at` datetime(3) NULL DEFAULT NULL COMMENT '消费者加锁时间',
  `locked_by` varchar(64) NULL DEFAULT NULL COMMENT '锁持有者标识（实例/worker）',
  `received_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '接收时间',
  `processed_at` datetime(3) NULL DEFAULT NULL COMMENT '处理完成时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_webhook_event`(`channel` ASC, `event_id` ASC) USING BTREE,
  INDEX `idx_wi_status`(`status` ASC, `received_at` ASC) USING BTREE,
  INDEX `idx_wi_retry`(`status` ASC, `next_retry_at` ASC) USING BTREE,
  INDEX `idx_wi_locked`(`locked_at` ASC, `locked_by` ASC) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = 'Webhook 异步收件箱' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for welcome_logs
-- ----------------------------
CREATE TABLE IF NOT EXISTS `welcome_logs` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '被识别顾客 → customers.id',
  `camera_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '触发摄像头 → cameras.id',
  `welcome_text` varchar(500) NOT NULL COMMENT '播报/展示的欢迎语（可含口味推荐）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '迎宾时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_welcome_customer`(`customer_id` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_welcome_camera`(`camera_id` ASC) USING BTREE,
  CONSTRAINT `fk_welcome_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_welcome_camera` FOREIGN KEY (`camera_id`) REFERENCES `cameras` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '人脸迎宾记录表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for customer_favorite_products
-- ----------------------------
CREATE TABLE IF NOT EXISTS `customer_favorite_products` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '餐品 → products.id',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '收藏时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_cfp_customer_product`(`customer_id` ASC, `product_id` ASC) USING BTREE,
  INDEX `ix_cfp_customer_store`(`customer_id` ASC, `store_id` ASC) USING BTREE,
  CONSTRAINT `fk_cfp_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_cfp_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_cfp_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '菜品收藏表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for invoice_requests
-- ----------------------------
CREATE TABLE IF NOT EXISTS `invoice_requests` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  `order_no` varchar(40) NOT NULL COMMENT '订单号快照',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `customer_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '顾客 → customers.id',
  `title_type` varchar(10) NOT NULL DEFAULT 'PERSON' COMMENT 'PERSON/COMPANY',
  `title` varchar(200) NOT NULL COMMENT '发票抬头',
  `tax_no` varchar(50) NULL DEFAULT NULL COMMENT '税号（单位抬头）',
  `email` varchar(100) NOT NULL COMMENT '接收邮箱',
  `amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '开票金额',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/ISSUED/REJECTED',
  `reject_reason` varchar(255) NULL DEFAULT NULL COMMENT '驳回原因',
  `issued_at` datetime(3) NULL DEFAULT NULL COMMENT '开票时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_invoice_order`(`order_id` ASC) USING BTREE,
  INDEX `ix_invoice_requests_store_status`(`store_id` ASC, `status` ASC) USING BTREE,
  CONSTRAINT `fk_invoice_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_invoice_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_invoice_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '电子发票开票申请表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for queue_tickets
-- ----------------------------
CREATE TABLE IF NOT EXISTS `queue_tickets` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `customer_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '顾客 → customers.id',
  `ticket_no` varchar(20) NOT NULL COMMENT '展示号（如 A08）',
  `number` int NOT NULL DEFAULT 1 COMMENT '当日序号',
  `issue_date` date NOT NULL COMMENT '取号日期',
  `people_count` int NOT NULL DEFAULT 1 COMMENT '用餐人数',
  `status` varchar(20) NOT NULL DEFAULT 'WAITING' COMMENT 'WAITING/CALLED/SEATED/CANCELLED',
  `issued_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '取号时间',
  `called_at` datetime(3) NULL DEFAULT NULL COMMENT '叫号时间',
  `seated_at` datetime(3) NULL DEFAULT NULL COMMENT '落座时间',
  `cancelled_at` datetime(3) NULL DEFAULT NULL COMMENT '取消时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_queue_store_date_number`(`store_id` ASC, `issue_date` ASC, `number` ASC) USING BTREE,
  INDEX `ix_queue_store_status`(`store_id` ASC, `status` ASC) USING BTREE,
  INDEX `ix_queue_customer`(`customer_id` ASC, `issue_date` ASC) USING BTREE,
  CONSTRAINT `fk_queue_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_queue_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '到店排队取号表' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for social_identity_types
-- ----------------------------
CREATE TABLE IF NOT EXISTS `social_identity_types` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `code` varchar(32) NOT NULL COMMENT '身份编码 SANITATION/RIDER/ELDER/TEACHER/STUDENT/MEDICAL/...（字典可扩展）',
  `name` varchar(50) NOT NULL COMMENT '展示名称',
  `sort_order` int NOT NULL DEFAULT 0 COMMENT '排序升序',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1启用 0停用',
  `default_verify_hints` json NULL COMMENT '建议核验凭证提示',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_sit_code`(`code` ASC) USING BTREE,
  INDEX `idx_sit_status_sort`(`status` ASC, `sort_order` ASC) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '社会贡献身份类型字典' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for customer_social_identities
-- ----------------------------
CREATE TABLE IF NOT EXISTS `customer_social_identities` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `identity_code` varchar(32) NOT NULL COMMENT '身份编码 → social_identity_types.code',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '认定门店 → stores.id；NULL=总部认定',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/ACTIVE/REJECTED/EXPIRED/REVOKED',
  `verify_method` varchar(50) NULL DEFAULT NULL COMMENT '核验方式 POS/WECHAT/ALIPAY/MINI/MANUAL',
  `credential_note` varchar(255) NULL DEFAULT NULL COMMENT '凭证备注（脱敏）',
  `credential_media_url` varchar(500) NULL DEFAULT NULL COMMENT '证件图路径（仅店长可读）',
  `valid_from` datetime(3) NULL DEFAULT NULL COMMENT '生效开始',
  `valid_to` datetime(3) NULL DEFAULT NULL COMMENT '生效结束',
  `reviewed_by` bigint UNSIGNED NULL DEFAULT NULL COMMENT '审核人 → users.id',
  `reviewed_at` datetime(3) NULL DEFAULT NULL COMMENT '审核时间',
  `reject_reason` varchar(255) NULL DEFAULT NULL COMMENT '拒绝原因',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_csi_customer_status`(`customer_id` ASC, `status` ASC) USING BTREE,
  INDEX `idx_csi_store_status`(`store_id` ASC, `status` ASC) USING BTREE,
  INDEX `idx_csi_code_status`(`identity_code` ASC, `status` ASC) USING BTREE,
  CONSTRAINT `fk_csi_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_csi_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_csi_reviewer` FOREIGN KEY (`reviewed_by`) REFERENCES `users` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '顾客社会贡献身份认定' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for social_benefit_rules
-- ----------------------------
CREATE TABLE IF NOT EXISTS `social_benefit_rules` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '门店 → stores.id；NULL=总部通用',
  `identity_code` varchar(32) NOT NULL COMMENT '适用身份编码',
  `name` varchar(100) NOT NULL COMMENT '规则名称',
  `time_windows` json NULL COMMENT '时段 [["06:00","23:59"],...]',
  `weekdays` json NULL COMMENT '星期 1-7；NULL=每天',
  `date_from` date NULL DEFAULT NULL COMMENT '生效日起',
  `date_to` date NULL DEFAULT NULL COMMENT '生效日止',
  `benefit_type` varchar(30) NOT NULL DEFAULT 'ORDER_DISCOUNT' COMMENT 'ORDER_DISCOUNT/COUPON/GIFT_ITEM/FREE_PRODUCT',
  `discount_rate` decimal(5, 2) NULL DEFAULT NULL COMMENT '折扣率 0.80=八折',
  `coupon_template_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '公益券模板',
  `gift_product_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '赠品餐品',
  `applicable_category_ids` json NULL COMMENT '适用分类；NULL=全部',
  `applicable_product_ids` json NULL COMMENT '适用餐品；NULL=全部',
  `daily_limit` int NOT NULL DEFAULT 1 COMMENT '每客每店每日限次',
  `monthly_budget` decimal(12, 2) NULL DEFAULT NULL COMMENT '月补贴预算',
  `budget_action` varchar(10) NOT NULL DEFAULT 'WARN' COMMENT '超预算 WARN/BLOCK',
  `stack_policy` varchar(30) NOT NULL DEFAULT 'EXCLUSIVE' COMMENT 'EXCLUSIVE=与券互斥、与会员折取更优；STACKABLE结算暂未接线',
  `max_subsidy_amount` decimal(10, 2) NULL DEFAULT NULL COMMENT '单次补贴上限',
  `priority` int NOT NULL DEFAULT 100 COMMENT '优先级，越小越优先',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1启用 0停用',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_sbr_store_code_status`(`store_id` ASC, `identity_code` ASC, `status` ASC) USING BTREE,
  INDEX `idx_sbr_priority`(`priority` ASC, `status` ASC) USING BTREE,
  CONSTRAINT `fk_sbr_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_sbr_coupon` FOREIGN KEY (`coupon_template_id`) REFERENCES `coupon_templates` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT,
  CONSTRAINT `fk_sbr_gift` FOREIGN KEY (`gift_product_id`) REFERENCES `products` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '社会贡献权益规则' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for social_benefit_redemptions
-- ----------------------------
CREATE TABLE IF NOT EXISTS `social_benefit_redemptions` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  `order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '订单 → orders.id',
  `identity_code` varchar(32) NOT NULL COMMENT '身份编码',
  `rule_id` bigint UNSIGNED NOT NULL COMMENT '规则 → social_benefit_rules.id',
  `benefit_type` varchar(30) NOT NULL COMMENT '权益类型快照',
  `subsidy_amount` decimal(10, 2) NOT NULL DEFAULT 0.00 COMMENT '补贴金额',
  `coupon_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '关联发放的券',
  `operator_user_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '操作员 → users.id',
  `idempotency_key` varchar(128) NOT NULL COMMENT '幂等键',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/NORMAL/REVERSED',
  `redeemed_at` datetime(3) NULL DEFAULT NULL COMMENT '正式核销时间（支付成功）',
  `meta` json NULL COMMENT '计价快照等',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_sbrd_idem`(`idempotency_key` ASC) USING BTREE,
  INDEX `idx_sbrd_customer_store_day`(`customer_id` ASC, `store_id` ASC, `created_at` ASC) USING BTREE,
  INDEX `idx_sbrd_order`(`order_id` ASC) USING BTREE,
  INDEX `idx_sbrd_rule_status`(`rule_id` ASC, `status` ASC) USING BTREE,
  CONSTRAINT `fk_sbrd_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_sbrd_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_sbrd_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT,
  CONSTRAINT `fk_sbrd_rule` FOREIGN KEY (`rule_id`) REFERENCES `social_benefit_rules` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT `fk_sbrd_coupon` FOREIGN KEY (`coupon_id`) REFERENCES `customer_coupons` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT,
  CONSTRAINT `fk_sbrd_operator` FOREIGN KEY (`operator_user_id`) REFERENCES `users` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '社会贡献权益核销台账' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for customer_segments
-- ----------------------------
CREATE TABLE IF NOT EXISTS `customer_segments` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '门店；NULL=总部',
  `code` varchar(64) NOT NULL COMMENT '业务键',

  `name` varchar(100) NOT NULL COMMENT '分群名称',
  `segment_type` varchar(20) NOT NULL DEFAULT 'DYNAMIC' COMMENT 'STATIC/DYNAMIC',
  `rules_json` json NULL COMMENT '条件树',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1启用 0停用',
  `estimated_count` int NULL DEFAULT NULL COMMENT '最近估人数',
  `materialized_at` datetime(3) NULL DEFAULT NULL COMMENT '最近物化时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_cseg_store_code`(`store_id` ASC, `code` ASC) USING BTREE,
  INDEX `idx_cseg_status`(`status` ASC) USING BTREE,
  CONSTRAINT `fk_cseg_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '客户分群' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for customer_segment_members
-- ----------------------------
CREATE TABLE IF NOT EXISTS `customer_segment_members` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `segment_id` bigint UNSIGNED NOT NULL COMMENT '分群 → customer_segments.id',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入群时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_csm_seg_customer`(`segment_id` ASC, `customer_id` ASC) USING BTREE,
  INDEX `idx_csm_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_csm_segment` FOREIGN KEY (`segment_id`) REFERENCES `customer_segments` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_csm_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '分群成员快照' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for marketing_campaigns
-- ----------------------------
CREATE TABLE IF NOT EXISTS `marketing_campaigns` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '门店；NULL=总部',
  `code` varchar(64) NOT NULL COMMENT '业务键',
  `code_key` varchar(96) NULL DEFAULT NULL COMMENT 'HQ:CODE 或 store_id:CODE（跨库唯一）',
  `code_key` varchar(96) NULL DEFAULT NULL COMMENT 'HQ:{CODE} 或 {store_id}:{CODE}，跨库唯一',
  `name` varchar(100) NOT NULL COMMENT '活动名',
  `campaign_type` varchar(30) NOT NULL DEFAULT 'MEMBER_DAY' COMMENT 'FESTIVAL/MEMBER_DAY/TIME_SLOT/SOCIAL/...',
  `status` varchar(20) NOT NULL DEFAULT 'DRAFT' COMMENT 'DRAFT/SCHEDULED/RUNNING/PAUSED/ENDED/CANCELLED',
  `segment_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '圈人分群',
  `coupon_template_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '发券模板',
  `schedule_mode` varchar(20) NOT NULL DEFAULT 'ABSOLUTE' COMMENT 'ABSOLUTE=绝对起止；ANNUAL_MD=每年月日（公历/农历）自动启停',
  `schedule_calendar` varchar(10) NOT NULL DEFAULT 'SOLAR' COMMENT 'SOLAR=公历 LUNAR=农历（仅 ANNUAL_MD）',
  `annual_start_month` tinyint UNSIGNED NULL DEFAULT NULL COMMENT '年度窗起月 1-12',
  `annual_start_day` tinyint UNSIGNED NULL DEFAULT NULL COMMENT '年度窗起日 1-31',
  `annual_end_month` tinyint UNSIGNED NULL DEFAULT NULL COMMENT '年度窗止月；空=仅起日当天',
  `annual_end_day` tinyint UNSIGNED NULL DEFAULT NULL COMMENT '年度窗止日',
  `start_at` datetime(3) NULL DEFAULT NULL COMMENT '绝对开始（ABSOLUTE）',
  `end_at` datetime(3) NULL DEFAULT NULL COMMENT '绝对结束（ABSOLUTE；到点 ENDED）',
  `budget_cap` decimal(12, 2) NULL DEFAULT NULL COMMENT '预算上限',
  `weekly_cap_override` int NULL DEFAULT NULL COMMENT '覆盖周频控',
  `boost_product_ids` json NULL COMMENT '推荐加权餐品',
  `remark` varchar(500) NULL DEFAULT NULL COMMENT '备注',
  `published_at` datetime(3) NULL DEFAULT NULL COMMENT '发布时间/启用年度排期时间',
  `created_by` bigint UNSIGNED NULL DEFAULT NULL COMMENT '创建人',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_mc_store_code`(`store_id` ASC, `code` ASC) USING BTREE,
  UNIQUE INDEX `uk_mc_code_key`(`code_key` ASC) USING BTREE,
  INDEX `idx_mc_status_time`(`status` ASC, `start_at` ASC, `end_at` ASC) USING BTREE,
  INDEX `idx_mc_schedule_mode`(`schedule_mode` ASC, `status` ASC) USING BTREE,
  CONSTRAINT `fk_mc_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_mc_segment` FOREIGN KEY (`segment_id`) REFERENCES `customer_segments` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT,
  CONSTRAINT `fk_mc_coupon` FOREIGN KEY (`coupon_template_id`) REFERENCES `coupon_templates` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT,
  CONSTRAINT `fk_mc_creator` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '营销活动' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for marketing_rules
-- ----------------------------
CREATE TABLE IF NOT EXISTS `marketing_rules` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `campaign_id` bigint UNSIGNED NOT NULL COMMENT '活动 → marketing_campaigns.id',
  `rule_type` varchar(30) NOT NULL DEFAULT 'SEGMENT' COMMENT 'SEGMENT/EVENT/CRON/WINDOW/SOCIAL_LINK',
  `config_json` json NULL COMMENT '规则配置',
  `priority` int NOT NULL DEFAULT 100 COMMENT '优先级',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1启用 0停用',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_mr_campaign`(`campaign_id` ASC, `status` ASC) USING BTREE,
  CONSTRAINT `fk_mr_campaign` FOREIGN KEY (`campaign_id`) REFERENCES `marketing_campaigns` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '营销活动规则' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for marketing_tasks
-- ----------------------------
CREATE TABLE IF NOT EXISTS `marketing_tasks` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `campaign_id` bigint UNSIGNED NOT NULL COMMENT '活动',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '目标顾客',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '门店快照',
  `action_type` varchar(30) NOT NULL DEFAULT 'ISSUE_COUPON' COMMENT 'ISSUE_COUPON/ISSUE_BUNDLE/SEND_SUBSCRIBE/...',
  `payload_json` json NULL COMMENT '动作参数',
  `idempotency_key` varchar(128) NOT NULL COMMENT '幂等键',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/RUNNING/SUCCESS/FAILED/SKIPPED/CANCELLED',
  `try_count` int NOT NULL DEFAULT 0 COMMENT '尝试次数',
  `last_error` varchar(500) NULL DEFAULT NULL COMMENT '最近错误',
  `result_json` json NULL COMMENT '结果',
  `scheduled_at` datetime(3) NULL DEFAULT NULL COMMENT '计划执行',
  `executed_at` datetime(3) NULL DEFAULT NULL COMMENT '执行完成',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_mt_idem`(`idempotency_key` ASC) USING BTREE,
  INDEX `idx_mt_status_sched`(`status` ASC, `scheduled_at` ASC) USING BTREE,
  INDEX `idx_mt_campaign`(`campaign_id` ASC, `status` ASC) USING BTREE,
  INDEX `idx_mt_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_mt_campaign` FOREIGN KEY (`campaign_id`) REFERENCES `marketing_campaigns` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_mt_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_mt_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '营销任务队列' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for marketing_message_logs
-- ----------------------------
CREATE TABLE IF NOT EXISTS `marketing_message_logs` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '门店',
  `campaign_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '活动；NULL=系统旅程',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客',
  `channel` varchar(20) NOT NULL DEFAULT 'WECHAT' COMMENT 'WECHAT/ALIPAY/SMS/LOG',
  `template_key` varchar(64) NOT NULL COMMENT '业务模板键',
  `template_id` varchar(120) NULL DEFAULT NULL COMMENT '平台模板ID',
  `payload_json` json NULL COMMENT '发送摘要（无敏感原文）',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/SUCCESS/FAILED/SKIPPED',
  `error_message` varchar(500) NULL DEFAULT NULL COMMENT '失败原因',
  `idempotency_key` varchar(128) NOT NULL COMMENT '幂等键',
  `sent_at` datetime(3) NULL DEFAULT NULL COMMENT '发送时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_mml_idem`(`idempotency_key` ASC) USING BTREE,
  INDEX `idx_mml_customer_sent`(`customer_id` ASC, `sent_at` ASC) USING BTREE,
  INDEX `idx_mml_campaign_status`(`campaign_id` ASC, `status` ASC) USING BTREE,
  CONSTRAINT `fk_mml_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT,
  CONSTRAINT `fk_mml_campaign` FOREIGN KEY (`campaign_id`) REFERENCES `marketing_campaigns` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT,
  CONSTRAINT `fk_mml_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '营销触达日志' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for campaign_attributions
-- ----------------------------
CREATE TABLE IF NOT EXISTS `campaign_attributions` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `campaign_id` bigint UNSIGNED NOT NULL COMMENT '活动',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '门店',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '订单',
  `attribution_type` varchar(20) NOT NULL DEFAULT 'COUPON' COMMENT 'COUPON/MESSAGE/SOCIAL',
  `coupon_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '归因券',
  `gross_amount` decimal(12, 2) NOT NULL DEFAULT 0.00 COMMENT '归因实收（支付）',
  `subsidy_amount` decimal(12, 2) NOT NULL DEFAULT 0.00 COMMENT '补贴/券面额',
  `net_amount` decimal(12, 2) NOT NULL DEFAULT 0.00 COMMENT '净贡献',
  `status` varchar(20) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/REVERSED',
  `idempotency_key` varchar(128) NOT NULL COMMENT '幂等键',
  `meta` json NULL COMMENT '扩展',
  `attributed_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '归因时间',
  `reversed_at` datetime(3) NULL DEFAULT NULL COMMENT '冲减时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_ca_idem`(`idempotency_key` ASC) USING BTREE,
  INDEX `idx_ca_campaign_status`(`campaign_id` ASC, `status` ASC) USING BTREE,
  INDEX `idx_ca_order`(`order_id` ASC) USING BTREE,
  INDEX `idx_ca_customer`(`customer_id` ASC) USING BTREE,
  CONSTRAINT `fk_ca_campaign` FOREIGN KEY (`campaign_id`) REFERENCES `marketing_campaigns` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_ca_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_ca_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT,
  CONSTRAINT `fk_ca_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_ca_coupon` FOREIGN KEY (`coupon_id`) REFERENCES `customer_coupons` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '营销活动归因' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for customer_lifecycle
-- ----------------------------
CREATE TABLE IF NOT EXISTS `customer_lifecycle` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  `lifecycle_code` varchar(20) NOT NULL DEFAULT 'GUEST' COMMENT 'GUEST/NEW/ACTIVE/AT_RISK/DORMANT/LOST',
  `rfm_segment` varchar(32) NULL DEFAULT NULL COMMENT 'RFM 分群标签',
  `r_score` tinyint NULL DEFAULT NULL COMMENT '近期分 1-5',
  `f_score` tinyint NULL DEFAULT NULL COMMENT '频次分 1-5',
  `m_score` tinyint NULL DEFAULT NULL COMMENT '金额分 1-5',
  `silence_days` int NOT NULL DEFAULT 0 COMMENT '距上次下单天数；无单=9999',
  `order_count_90d` int NOT NULL DEFAULT 0 COMMENT '近90天订单数',
  `pay_amount_90d` decimal(12, 2) NOT NULL DEFAULT 0.00 COMMENT '近90天实付',
  `computed_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '计算时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_cl_customer`(`customer_id` ASC) USING BTREE,
  INDEX `idx_cl_lifecycle`(`lifecycle_code` ASC) USING BTREE,
  INDEX `idx_cl_rfm`(`rfm_segment` ASC) USING BTREE,
  INDEX `idx_cl_silence`(`silence_days` ASC) USING BTREE,
  CONSTRAINT `fk_cl_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '顾客生命周期与RFM快照' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for point_exchange_options
-- ----------------------------
CREATE TABLE IF NOT EXISTS `point_exchange_options` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `store_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '门店 → stores.id；NULL=总部全局选项',
  `name` varchar(100) NOT NULL COMMENT '选项名称',
  `template_id` bigint UNSIGNED NOT NULL COMMENT '券模板 → coupon_templates.id',
  `points_cost` int NOT NULL COMMENT '所需积分',
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  `sort_order` int NOT NULL DEFAULT 0 COMMENT '排序（升序）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_peo_store_name`(`store_id` ASC, `name` ASC) USING BTREE,
  INDEX `idx_peo_store_status`(`store_id` ASC, `status` ASC, `sort_order` ASC) USING BTREE,
  INDEX `idx_peo_template`(`template_id` ASC) USING BTREE,
  CONSTRAINT `fk_peo_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_peo_template` FOREIGN KEY (`template_id`) REFERENCES `coupon_templates` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '积分兑换选项' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for role_delegations
-- ----------------------------
CREATE TABLE IF NOT EXISTS `role_delegations` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `grantor_user_id` bigint UNSIGNED NOT NULL COMMENT '委托人 → users.id',
  `delegatee_user_id` bigint UNSIGNED NOT NULL COMMENT '被委托人 → users.id',
  `permission_codes` json NOT NULL COMMENT '临时授予的权限码列表',
  `starts_at` datetime(3) NOT NULL COMMENT '生效时间',
  `ends_at` datetime(3) NOT NULL COMMENT '失效时间',
  `status` varchar(20) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/REVOKED/EXPIRED',
  `reason` varchar(255) NULL DEFAULT NULL COMMENT '授权原因',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_rd_delegatee_status_ends`(`delegatee_user_id` ASC, `status` ASC, `ends_at` ASC) USING BTREE,
  INDEX `idx_rd_grantor`(`grantor_user_id` ASC) USING BTREE,
  CONSTRAINT `fk_rd_grantor` FOREIGN KEY (`grantor_user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_rd_delegatee` FOREIGN KEY (`delegatee_user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '临时授权/委派' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for payment_reconcile_batches
-- ----------------------------
CREATE TABLE IF NOT EXISTS `payment_reconcile_batches` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `channel` varchar(20) NOT NULL COMMENT 'WECHAT/ALIPAY',
  `bill_date` date NOT NULL COMMENT '对账日（渠道账单日）',
  `scope_store_id` bigint UNSIGNED NOT NULL DEFAULT 0 COMMENT '对账范围：0总部全平台，正数门店ID',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/DONE/FAILED',
  `local_count` int NOT NULL DEFAULT 0 COMMENT '本地成功支付笔数',
  `channel_count` int NOT NULL DEFAULT 0 COMMENT '渠道账单笔数',
  `mismatch_count` int NOT NULL DEFAULT 0 COMMENT '差异笔数',
  `source_filename` varchar(255) NULL DEFAULT NULL COMMENT '导入账单文件名',
  `error_message` varchar(500) NULL DEFAULT NULL COMMENT '失败原因',
  `created_by` bigint UNSIGNED NULL DEFAULT NULL COMMENT '操作人',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_prb_channel_bill_store`(`channel` ASC, `bill_date` ASC, `scope_store_id` ASC) USING BTREE,
  INDEX `idx_prb_status_date`(`status` ASC, `bill_date` ASC) USING BTREE,
  INDEX `idx_prb_scope_store`(`scope_store_id` ASC) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '支付渠道对账批次' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for payment_reconcile_diffs
-- ----------------------------
CREATE TABLE IF NOT EXISTS `payment_reconcile_diffs` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `batch_id` bigint UNSIGNED NOT NULL COMMENT '批次 → payment_reconcile_batches.id',
  `diff_type` varchar(32) NOT NULL COMMENT 'LONG/SHORT/AMOUNT_MISMATCH/FEE',
  `local_payment_no` varchar(64) NULL DEFAULT NULL COMMENT '本地支付单号',
  `channel_trade_no` varchar(128) NULL DEFAULT NULL COMMENT '渠道交易号',
  `local_amount` decimal(12, 2) NULL DEFAULT NULL COMMENT '本地金额',
  `channel_amount` decimal(12, 2) NULL DEFAULT NULL COMMENT '渠道金额',
  `detail` json NULL COMMENT '扩展差异详情',
  `status` varchar(20) NOT NULL DEFAULT 'OPEN' COMMENT 'OPEN/RESOLVED/IGNORED',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `idx_prd_batch_type`(`batch_id` ASC, `diff_type` ASC) USING BTREE,
  INDEX `idx_prd_status`(`status` ASC) USING BTREE,
  CONSTRAINT `fk_prd_batch` FOREIGN KEY (`batch_id`) REFERENCES `payment_reconcile_batches` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '支付对账差异工单' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for archived_order_snapshots
-- ----------------------------
CREATE TABLE IF NOT EXISTS `archived_order_snapshots` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `order_id` bigint UNSIGNED NOT NULL COMMENT '原订单 id（热表删除后仍可查）',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店',
  `order_no` varchar(64) NOT NULL COMMENT '订单号',
  `status` varchar(32) NOT NULL COMMENT '归档时订单状态',
  `payment_status` varchar(32) NULL DEFAULT NULL COMMENT '归档时支付状态',
  `original_created_at` datetime(3) NOT NULL COMMENT '原下单时间',
  `payload` json NOT NULL COMMENT '订单+明细+支付摘要快照',
  `archived_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '归档时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_aos_order_id`(`order_id` ASC) USING BTREE,
  UNIQUE INDEX `uk_aos_order_no`(`order_no` ASC) USING BTREE,
  INDEX `idx_aos_store_created`(`store_id` ASC, `original_created_at` ASC) USING BTREE,
  INDEX `idx_aos_archived_at`(`archived_at` ASC) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '历史订单冷库快照' ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for leader_fencing_state
-- ----------------------------
CREATE TABLE IF NOT EXISTS `leader_fencing_state` (
  `id` int NOT NULL COMMENT '单例主键',
  `epoch` bigint NOT NULL DEFAULT 0 COMMENT '数据库 fencing epoch',
  `owner_id` varchar(64) NULL DEFAULT NULL COMMENT '当前 leader owner token',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = 'Leader fencing 状态' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for marketing_campaign_stores
-- ----------------------------
CREATE TABLE IF NOT EXISTS `marketing_campaign_stores` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `campaign_id` bigint UNSIGNED NOT NULL COMMENT '营销活动',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '参与范围门店',
  `included` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否纳入活动',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_mcs_camp_store`(`campaign_id` ASC, `store_id` ASC) USING BTREE,
  INDEX `idx_mcs_store`(`store_id` ASC, `campaign_id` ASC) USING BTREE,
  CONSTRAINT `fk_mcs_campaign` FOREIGN KEY (`campaign_id`) REFERENCES `marketing_campaigns` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_mcs_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '营销活动参与门店范围' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for marketing_flash_sales
-- ----------------------------
CREATE TABLE IF NOT EXISTS `marketing_flash_sales` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `campaign_id` bigint UNSIGNED NOT NULL COMMENT '关联营销活动',
  `product_id` bigint UNSIGNED NOT NULL COMMENT '抢购商品',
  `per_customer_limit` int NOT NULL DEFAULT 1 COMMENT '每位顾客限购数量',
  `claim_valid_minutes` int NOT NULL DEFAULT 30 COMMENT '抢购资格有效分钟数',
  `verify_deadline_at` datetime(3) NULL DEFAULT NULL COMMENT '核销截止时间',
  `notice` varchar(200) NULL DEFAULT NULL COMMENT '活动提示',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_mfs_campaign`(`campaign_id` ASC) USING BTREE,
  CONSTRAINT `fk_mfs_campaign` FOREIGN KEY (`campaign_id`) REFERENCES `marketing_campaigns` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_mfs_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '限量抢购配置' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for marketing_flash_sale_stocks
-- ----------------------------
CREATE TABLE IF NOT EXISTS `marketing_flash_sale_stocks` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `campaign_id` bigint UNSIGNED NOT NULL COMMENT '营销活动',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '门店',
  `product_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '门店专属抢购商品',
  `total_quantity` int NOT NULL DEFAULT 0 COMMENT '总库存',
  `remaining_quantity` int NOT NULL DEFAULT 0 COMMENT '剩余库存',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_mfss_camp_store`(`campaign_id` ASC, `store_id` ASC) USING BTREE,
  INDEX `idx_mfss_store`(`store_id` ASC, `campaign_id` ASC) USING BTREE,
  INDEX `ix_mfss_product`(`product_id` ASC) USING BTREE,
  CONSTRAINT `fk_mfss_campaign` FOREIGN KEY (`campaign_id`) REFERENCES `marketing_campaigns` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_mfss_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_mfss_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '抢购门店库存' ROW_FORMAT = Dynamic;
-- ----------------------------
-- Table structure for marketing_flash_sale_claims
-- ----------------------------
CREATE TABLE IF NOT EXISTS `marketing_flash_sale_claims` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  `campaign_id` bigint UNSIGNED NOT NULL COMMENT '营销活动',
  `store_id` bigint UNSIGNED NOT NULL COMMENT '参与门店',
  `customer_id` bigint UNSIGNED NOT NULL COMMENT '顾客',
  `unit_no` int NOT NULL DEFAULT 1 COMMENT '每人第几份资格',
  `claim_no` varchar(40) NOT NULL COMMENT '抢购资格编号',
  `status` varchar(20) NOT NULL DEFAULT 'CLAIMED' COMMENT 'CLAIMED/USED/EXPIRED/CANCELLED',
  `claimed_at` datetime(3) NOT NULL COMMENT '领取时间',
  `expires_at` datetime(3) NOT NULL COMMENT '资格过期时间',
  `verify_deadline_at` datetime(3) NULL DEFAULT NULL COMMENT '核销截止时间',
  `order_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '核销订单',
  `verified_at` datetime(3) NULL DEFAULT NULL COMMENT '核销时间',
  `verified_by` bigint UNSIGNED NULL DEFAULT NULL COMMENT '核销操作人',
  `client_request_id` varchar(64) NULL DEFAULT NULL COMMENT '客户端幂等请求号',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_mfsc_cust_unit`(`campaign_id` ASC, `customer_id` ASC, `unit_no` ASC) USING BTREE,
  UNIQUE INDEX `uk_mfsc_no`(`claim_no` ASC) USING BTREE,
  UNIQUE INDEX `uk_mfsc_req`(`client_request_id` ASC) USING BTREE,
  INDEX `idx_mfsc_customer_status`(`customer_id` ASC, `status` ASC) USING BTREE,
  INDEX `idx_mfsc_status_expire`(`status` ASC, `expires_at` ASC) USING BTREE,
  INDEX `idx_mfsc_campaign_store`(`campaign_id` ASC, `store_id` ASC, `status` ASC) USING BTREE,
  CONSTRAINT `fk_mfsc_campaign` FOREIGN KEY (`campaign_id`) REFERENCES `marketing_campaigns` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_mfsc_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_mfsc_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT `fk_mfsc_verified_by` FOREIGN KEY (`verified_by`) REFERENCES `users` (`id`) ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '限量抢购资格' ROW_FORMAT = Dynamic;

-- END CANONICAL SCHEMA
SET FOREIGN_KEY_CHECKS = 1;


-- 0040/0041 marketing flash-sale schema (kept in sync with Alembic migrations)
CREATE TABLE IF NOT EXISTS `marketing_campaign_stores` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
  `campaign_id` bigint UNSIGNED NOT NULL,
  `store_id` bigint UNSIGNED NOT NULL,
  `included` tinyint(1) NOT NULL DEFAULT 0 COMMENT '0=排除该门店（总部活动默认全店参与）',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_mcs_camp_store`(`campaign_id` ASC, `store_id` ASC) USING BTREE,
  INDEX `idx_mcs_store`(`store_id` ASC, `campaign_id` ASC) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='活动参与门店例外（总部活动默认全店参与）';

CREATE TABLE IF NOT EXISTS `marketing_flash_sales` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
  `campaign_id` bigint UNSIGNED NOT NULL,
  `product_id` bigint UNSIGNED NOT NULL,
  `per_customer_limit` int NOT NULL DEFAULT 1 COMMENT '每人限购数量',
  `claim_valid_minutes` int NOT NULL DEFAULT 30 COMMENT '资格有效分钟数',
  `verify_deadline_at` datetime(3) NULL DEFAULT NULL COMMENT '绝对核销截止',
  `notice` varchar(200) NULL DEFAULT NULL,
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_mfs_campaign`(`campaign_id` ASC) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='限量免费抢购配置';

CREATE TABLE IF NOT EXISTS `marketing_flash_sale_stocks` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
  `campaign_id` bigint UNSIGNED NOT NULL,
  `store_id` bigint UNSIGNED NOT NULL,
  `product_id` bigint UNSIGNED NULL DEFAULT NULL COMMENT '本店抢购餐品；空=回退活动配置',
  `total_quantity` int NOT NULL DEFAULT 0,
  `remaining_quantity` int NOT NULL DEFAULT 0,
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_mfss_camp_store`(`campaign_id` ASC, `store_id` ASC) USING BTREE,
  INDEX `idx_mfss_store`(`store_id` ASC, `campaign_id` ASC) USING BTREE,
  INDEX `ix_mfss_product`(`product_id` ASC) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='抢购门店库存（条件 UPDATE 原子扣减）';

CREATE TABLE IF NOT EXISTS `marketing_flash_sale_claims` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
  `campaign_id` bigint UNSIGNED NOT NULL,
  `store_id` bigint UNSIGNED NOT NULL,
  `customer_id` bigint UNSIGNED NOT NULL,
  `unit_no` int NOT NULL DEFAULT 1 COMMENT '同一顾客第几份，配合唯一键限购',
  `claim_no` varchar(40) NOT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'CLAIMED',
  `claimed_at` datetime(3) NOT NULL,
  `expires_at` datetime(3) NOT NULL,
  `verify_deadline_at` datetime(3) NULL DEFAULT NULL,
  `order_id` bigint UNSIGNED NULL DEFAULT NULL,
  `verified_at` datetime(3) NULL DEFAULT NULL,
  `verified_by` bigint UNSIGNED NULL DEFAULT NULL,
  `client_request_id` varchar(64) NULL DEFAULT NULL,
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_mfsc_cust_unit`(`campaign_id` ASC, `customer_id` ASC, `unit_no` ASC) USING BTREE,
  UNIQUE INDEX `uk_mfsc_no`(`claim_no` ASC) USING BTREE,
  UNIQUE INDEX `uk_mfsc_req`(`client_request_id` ASC) USING BTREE,
  INDEX `idx_mfsc_customer_status`(`customer_id` ASC, `status` ASC) USING BTREE,
  INDEX `idx_mfsc_status_expire`(`status` ASC, `expires_at` ASC) USING BTREE,
  INDEX `idx_mfsc_campaign_store`(`campaign_id` ASC, `store_id` ASC, `status` ASC) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='顾客抢购资格';

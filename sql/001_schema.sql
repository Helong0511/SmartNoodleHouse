-- ============================================================================
-- 智能面馆 SmartNoodleHouse — 数据库结构（全量 DDL）
-- ============================================================================
-- 适用：MySQL 8.0+ / utf8mb4
-- 说明：本文件为「空库一键建表」最终态，已合并原 001～016 中所有表结构与索引。
-- 执行顺序：
--   1) 000_create_db_grant.sql   （root：建库 + 授权，可选）
--   2) 001_schema.sql            （本文件：结构）
--   3) 002_seed_test.sql         （测试/开发种子数据）
-- ============================================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE DATABASE IF NOT EXISTS smart_noodle_house
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE smart_noodle_house;

-- ============================================================================
-- 一、系统用户与 RBAC（角色 / 权限 / 员工）
-- ============================================================================

-- 角色定义
CREATE TABLE IF NOT EXISTS roles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(50) NOT NULL UNIQUE COMMENT '角色编码：SUPER_ADMIN/STORE_ADMIN/CASHIER/KITCHEN/OPERATOR',
  name VARCHAR(100) NOT NULL COMMENT '角色名称',
  description VARCHAR(255) DEFAULT NULL COMMENT '说明',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='后台角色';

-- 权限点
CREATE TABLE IF NOT EXISTS permissions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(100) NOT NULL UNIQUE COMMENT '权限码，如 product:read',
  name VARCHAR(100) NOT NULL COMMENT '权限名称',
  module VARCHAR(50) NOT NULL COMMENT '所属模块',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='权限点';

-- 角色 ↔ 权限
CREATE TABLE IF NOT EXISTS role_permissions (
  role_id BIGINT UNSIGNED NOT NULL,
  permission_id BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (role_id, permission_id),
  CONSTRAINT fk_rp_role FOREIGN KEY (role_id) REFERENCES roles(id),
  CONSTRAINT fk_rp_perm FOREIGN KEY (permission_id) REFERENCES permissions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色权限关联';

-- 后台员工账号
CREATE TABLE IF NOT EXISTS users (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(50) NOT NULL UNIQUE COMMENT '登录名',
  password_hash VARCHAR(255) NOT NULL COMMENT '密码哈希',
  display_name VARCHAR(100) NOT NULL COMMENT '显示名',
  role_id BIGINT UNSIGNED NOT NULL COMMENT '角色 ID',
  store_id BIGINT UNSIGNED DEFAULT NULL COMMENT '绑定门店；NULL=超管可跨店',
  token_version INT NOT NULL DEFAULT 0 COMMENT 'JWT 版本，递增可作废旧令牌',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  last_login_at DATETIME(3) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  INDEX idx_users_store (store_id),
  CONSTRAINT fk_users_role FOREIGN KEY (role_id) REFERENCES roles(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='后台用户';

-- 操作审计日志
CREATE TABLE IF NOT EXISTS operation_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT UNSIGNED DEFAULT NULL COMMENT '操作人',
  action VARCHAR(100) NOT NULL COMMENT '动作编码',
  module VARCHAR(50) NOT NULL COMMENT '模块',
  target_type VARCHAR(50) DEFAULT NULL COMMENT '目标类型',
  target_id BIGINT UNSIGNED DEFAULT NULL COMMENT '目标 ID',
  ip VARCHAR(45) DEFAULT NULL,
  user_agent VARCHAR(500) DEFAULT NULL,
  request_id VARCHAR(64) DEFAULT NULL,
  detail JSON DEFAULT NULL COMMENT '详情 JSON',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_op_logs_user (user_id, created_at),
  INDEX idx_op_logs_module (module, created_at),
  INDEX idx_op_logs_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='操作日志';

-- ============================================================================
-- 二、门店与桌台
-- ============================================================================

CREATE TABLE IF NOT EXISTS stores (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  store_no VARCHAR(32) NOT NULL UNIQUE COMMENT '门店编号',
  name VARCHAR(100) NOT NULL COMMENT '门店名称',
  address VARCHAR(255) DEFAULT NULL,
  phone VARCHAR(20) DEFAULT NULL,
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1=营业 0=停用',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='门店';

-- 桌台（扫码点餐令牌）
CREATE TABLE IF NOT EXISTS tables (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  store_id BIGINT UNSIGNED NOT NULL,
  table_no VARCHAR(20) NOT NULL COMMENT '桌号',
  table_name VARCHAR(50) NOT NULL COMMENT '桌台显示名',
  qr_token VARCHAR(64) NOT NULL UNIQUE COMMENT '扫码令牌（开发可用桌号作 token）',
  wechat_qr_url VARCHAR(500) DEFAULT NULL COMMENT '微信小程序路径',
  alipay_qr_url VARCHAR(500) DEFAULT NULL COMMENT '支付宝小程序路径',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1=启用 0=禁用',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_store_table (store_id, table_no),
  INDEX idx_tables_token (qr_token),
  CONSTRAINT fk_tables_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='桌台';

-- 门店级业务参数（KV）
CREATE TABLE IF NOT EXISTS store_settings (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  store_id BIGINT UNSIGNED NOT NULL,
  setting_key VARCHAR(100) NOT NULL COMMENT '参数键',
  setting_value VARCHAR(500) NOT NULL COMMENT '参数值',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_store_setting (store_id, setting_key),
  CONSTRAINT fk_ss_store FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='门店业务参数';

-- ============================================================================
-- 三、顾客 / 会员 / 第三方账号
-- ============================================================================

CREATE TABLE IF NOT EXISTS member_levels (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(50) NOT NULL COMMENT '等级名称',
  min_points INT NOT NULL DEFAULT 0 COMMENT '所需积分下限',
  discount_rate DECIMAL(5,4) NOT NULL DEFAULT 1.0000 COMMENT '折扣率，1=无折扣',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='会员等级';

CREATE TABLE IF NOT EXISTS customers (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_no VARCHAR(40) NOT NULL UNIQUE COMMENT '顾客编号',
  display_name VARCHAR(100) DEFAULT NULL COMMENT '展示名',
  nickname VARCHAR(100) DEFAULT NULL COMMENT '昵称',
  avatar_url VARCHAR(500) DEFAULT NULL,
  phone VARCHAR(20) DEFAULT NULL,
  member_level_id BIGINT UNSIGNED DEFAULT NULL,
  status TINYINT NOT NULL DEFAULT 1,
  last_order_at DATETIME(3) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  INDEX idx_customers_phone (phone),
  INDEX idx_customers_last_order (last_order_at),
  CONSTRAINT fk_customers_level FOREIGN KEY (member_level_id) REFERENCES member_levels(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='顾客';

CREATE TABLE IF NOT EXISTS customer_wechat_accounts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id BIGINT UNSIGNED NOT NULL,
  openid VARCHAR(64) NOT NULL UNIQUE,
  unionid VARCHAR(64) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  INDEX idx_wechat_unionid (unionid),
  CONSTRAINT fk_cwa_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='微信小程序账号绑定';

CREATE TABLE IF NOT EXISTS customer_alipay_accounts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id BIGINT UNSIGNED NOT NULL,
  alipay_user_id VARCHAR(64) NOT NULL UNIQUE,
  alipay_union_id VARCHAR(64) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_caa_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='支付宝小程序账号绑定';

-- 口味偏好（下单后统计）
CREATE TABLE IF NOT EXISTS customer_preferences (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id BIGINT UNSIGNED NOT NULL,
  category VARCHAR(50) NOT NULL COMMENT '规格组名称快照',
  option_code VARCHAR(50) NOT NULL COMMENT '选项编码',
  option_name VARCHAR(50) DEFAULT NULL COMMENT '选项名称快照',
  preference_score DECIMAL(5,4) NOT NULL DEFAULT 0.0000 COMMENT '偏好分',
  order_count INT NOT NULL DEFAULT 0 COMMENT '点选次数',
  last_used_at DATETIME(3) DEFAULT NULL,
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_cust_pref (customer_id, category, option_code),
  INDEX idx_pref_customer (customer_id, category),
  INDEX idx_customer_prefs_score (customer_id, preference_score DESC),
  CONSTRAINT fk_pref_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='顾客口味偏好';

-- ============================================================================
-- 四、商品 / 分类 / 规格
-- ============================================================================

CREATE TABLE IF NOT EXISTS product_categories (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  store_id BIGINT UNSIGNED NOT NULL COMMENT '所属门店',
  name VARCHAR(100) NOT NULL,
  description VARCHAR(255) DEFAULT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  status TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  INDEX idx_cat_store (store_id, sort_order, status),
  CONSTRAINT fk_cat_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品分类（按门店）';

CREATE TABLE IF NOT EXISTS products (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  store_id BIGINT UNSIGNED NOT NULL COMMENT '所属门店',
  category_id BIGINT UNSIGNED NOT NULL,
  product_no VARCHAR(32) NOT NULL COMMENT '店内商品编号',
  name VARCHAR(100) NOT NULL,
  description TEXT DEFAULT NULL,
  main_image VARCHAR(500) DEFAULT NULL COMMENT '主图路径',
  price DECIMAL(10,2) NOT NULL COMMENT '售价',
  original_price DECIMAL(10,2) DEFAULT NULL COMMENT '原价',
  stock INT NOT NULL DEFAULT 999 COMMENT '库存，999=不限',
  box_price DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '餐盒费（打包）',
  min_order_count INT NOT NULL DEFAULT 1 COMMENT '起售份数',
  status VARCHAR(20) NOT NULL DEFAULT 'ON_SALE' COMMENT 'ON_SALE/OFF_SALE/SOLD_OUT',
  is_recommended TINYINT NOT NULL DEFAULT 0,
  is_hot TINYINT NOT NULL DEFAULT 0,
  sort_order INT NOT NULL DEFAULT 0,
  sales_count INT NOT NULL DEFAULT 0,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_products_store_no (store_id, product_no),
  INDEX idx_products_store (store_id, status, sort_order),
  INDEX idx_products_category (category_id, status, sort_order),
  CONSTRAINT fk_products_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_products_category FOREIGN KEY (category_id) REFERENCES product_categories(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品（按门店）';

CREATE TABLE IF NOT EXISTS product_images (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  product_id BIGINT UNSIGNED NOT NULL,
  image_url VARCHAR(500) NOT NULL,
  image_type VARCHAR(20) NOT NULL DEFAULT 'DETAIL' COMMENT 'MAIN/DETAIL/OPTION',
  sort_order INT NOT NULL DEFAULT 0,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_pi_product (product_id, sort_order),
  CONSTRAINT fk_pi_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品图片';

-- 分店库存覆盖（可选；未配置则用 products.stock）
CREATE TABLE IF NOT EXISTS store_product_stocks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  store_id BIGINT UNSIGNED NOT NULL,
  product_id BIGINT UNSIGNED NOT NULL,
  stock INT NOT NULL DEFAULT 999 COMMENT '分店库存，999=不限',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1本店在售 0本店停售',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_store_product (store_id, product_id),
  INDEX idx_sps_product (product_id),
  CONSTRAINT fk_sps_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_sps_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='分店商品库存';

CREATE TABLE IF NOT EXISTS option_groups (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(50) NOT NULL COMMENT '规格组名称',
  code VARCHAR(50) NOT NULL UNIQUE COMMENT '如 NOODLE_AMOUNT/SPICY',
  selection_type VARCHAR(20) NOT NULL DEFAULT 'SINGLE' COMMENT 'SINGLE/MULTIPLE',
  is_required TINYINT NOT NULL DEFAULT 1,
  sort_order INT NOT NULL DEFAULT 0,
  status TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='规格组';

CREATE TABLE IF NOT EXISTS options (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  group_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(50) NOT NULL,
  code VARCHAR(50) NOT NULL,
  extra_price DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '加价',
  sort_order INT NOT NULL DEFAULT 0,
  status TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_group_code (group_id, code),
  CONSTRAINT fk_options_group FOREIGN KEY (group_id) REFERENCES option_groups(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='规格选项';

CREATE TABLE IF NOT EXISTS product_option_groups (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  product_id BIGINT UNSIGNED NOT NULL,
  group_id BIGINT UNSIGNED NOT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  UNIQUE KEY uk_product_group (product_id, group_id),
  CONSTRAINT fk_pog_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
  CONSTRAINT fk_pog_group FOREIGN KEY (group_id) REFERENCES option_groups(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品绑定规格组';

-- ============================================================================
-- 五、订单 / 支付 / 退款
-- ============================================================================

CREATE TABLE IF NOT EXISTS orders (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  order_no VARCHAR(40) NOT NULL UNIQUE COMMENT '业务单号',
  customer_id BIGINT UNSIGNED DEFAULT NULL,
  table_id BIGINT UNSIGNED DEFAULT NULL COMMENT '堂食桌台；外卖可空',
  store_id BIGINT UNSIGNED NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING_PAYMENT' COMMENT '订单状态机',
  dining_type VARCHAR(20) NOT NULL DEFAULT 'DINE_IN' COMMENT 'DINE_IN堂食 / TAKEAWAY打包',
  subtotal DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  discount_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  packing_fee DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '打包费合计',
  total_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  payment_status VARCHAR(20) NOT NULL DEFAULT 'UNPAID',
  payment_method VARCHAR(20) DEFAULT NULL,
  channel VARCHAR(20) DEFAULT NULL COMMENT 'WECHAT/ALIPAY/MEITUAN/ELEME/H5',
  remark VARCHAR(500) DEFAULT NULL,
  idempotency_key VARCHAR(64) DEFAULT NULL COMMENT '下单幂等键',
  ordered_at DATETIME(3) DEFAULT NULL,
  paid_at DATETIME(3) DEFAULT NULL,
  completed_at DATETIME(3) DEFAULT NULL,
  cancelled_at DATETIME(3) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_idempotency (idempotency_key),
  INDEX idx_orders_customer (customer_id, created_at),
  INDEX idx_orders_table (table_id, created_at),
  INDEX idx_orders_status (status, created_at),
  INDEX idx_orders_payment (payment_status, created_at),
  INDEX idx_orders_status_paid (status, paid_at),
  INDEX idx_orders_store_created (store_id, created_at),
  CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers(id),
  CONSTRAINT fk_orders_table FOREIGN KEY (table_id) REFERENCES tables(id),
  CONSTRAINT fk_orders_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单';

CREATE TABLE IF NOT EXISTS order_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  order_id BIGINT UNSIGNED NOT NULL,
  product_id BIGINT UNSIGNED NOT NULL,
  product_name_snapshot VARCHAR(100) NOT NULL,
  unit_price_snapshot DECIMAL(10,2) NOT NULL,
  quantity INT NOT NULL DEFAULT 1,
  subtotal DECIMAL(10,2) NOT NULL,
  remark VARCHAR(255) DEFAULT NULL COMMENT '单品备注',
  INDEX idx_oi_order (order_id),
  CONSTRAINT fk_oi_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
  CONSTRAINT fk_oi_product FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单明细';

CREATE TABLE IF NOT EXISTS order_item_options (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  order_item_id BIGINT UNSIGNED NOT NULL,
  group_name_snapshot VARCHAR(50) NOT NULL,
  option_name_snapshot VARCHAR(50) NOT NULL,
  option_code VARCHAR(50) NOT NULL,
  extra_price_snapshot DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  quantity INT NOT NULL DEFAULT 1,
  INDEX idx_oio_item (order_item_id),
  CONSTRAINT fk_oio_item FOREIGN KEY (order_item_id) REFERENCES order_items(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单明细规格快照';

CREATE TABLE IF NOT EXISTS order_status_history (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  order_id BIGINT UNSIGNED NOT NULL,
  from_status VARCHAR(20) DEFAULT NULL,
  to_status VARCHAR(20) NOT NULL,
  source VARCHAR(30) NOT NULL COMMENT 'STAFF/PAYMENT/SYSTEM/DELIVERY',
  operator_id BIGINT UNSIGNED DEFAULT NULL,
  remark VARCHAR(255) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_osh_order (order_id, created_at),
  CONSTRAINT fk_osh_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单状态变更历史';

CREATE TABLE IF NOT EXISTS payments (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  payment_no VARCHAR(40) NOT NULL UNIQUE COMMENT '支付单号',
  order_id BIGINT UNSIGNED NOT NULL,
  customer_id BIGINT UNSIGNED DEFAULT NULL,
  provider VARCHAR(20) NOT NULL COMMENT 'WECHAT/ALIPAY/MOCK',
  channel VARCHAR(30) NOT NULL,
  amount DECIMAL(10,2) NOT NULL,
  currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
  provider_trade_no VARCHAR(64) DEFAULT NULL,
  provider_order_no VARCHAR(64) DEFAULT NULL,
  callback_raw JSON DEFAULT NULL,
  paid_at DATETIME(3) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_provider_trade (provider, provider_trade_no),
  INDEX idx_payments_order (order_id),
  INDEX idx_payments_no (payment_no),
  INDEX idx_payments_order_status (order_id, status),
  INDEX idx_payments_created (created_at),
  CONSTRAINT fk_payments_order FOREIGN KEY (order_id) REFERENCES orders(id),
  CONSTRAINT fk_payments_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='支付单';

CREATE TABLE IF NOT EXISTS payment_refunds (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  refund_no VARCHAR(40) NOT NULL UNIQUE COMMENT '退款单号',
  payment_id BIGINT UNSIGNED NOT NULL,
  order_id BIGINT UNSIGNED NOT NULL,
  amount DECIMAL(10,2) NOT NULL,
  reason VARCHAR(255) DEFAULT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
  provider_refund_no VARCHAR(64) DEFAULT NULL,
  callback_raw JSON DEFAULT NULL COMMENT '平台退款回调/查询原文',
  fail_reason VARCHAR(255) DEFAULT NULL COMMENT '失败或关闭原因',
  succeeded_at DATETIME(3) DEFAULT NULL COMMENT '退款成功确认时间',
  operator_id BIGINT UNSIGNED DEFAULT NULL COMMENT '发起退款的员工用户 ID',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  INDEX idx_refunds_status (status),
  INDEX idx_refunds_payment (payment_id),
  INDEX idx_refunds_operator (operator_id),
  CONSTRAINT fk_refund_payment FOREIGN KEY (payment_id) REFERENCES payments(id),
  CONSTRAINT fk_refund_order FOREIGN KEY (order_id) REFERENCES orders(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='支付退款单';

CREATE TABLE IF NOT EXISTS payment_status_history (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  payment_id BIGINT UNSIGNED NOT NULL,
  from_status VARCHAR(20) DEFAULT NULL,
  to_status VARCHAR(20) NOT NULL,
  source VARCHAR(30) NOT NULL COMMENT 'CREATE/CALLBACK/REFUND/QUERY',
  remark VARCHAR(255) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_psh_payment (payment_id, created_at),
  CONSTRAINT fk_psh_payment FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='支付状态变更历史';

-- 支付回调幂等回执（与 Redis 双保险）
CREATE TABLE IF NOT EXISTS payment_callback_receipts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  provider VARCHAR(20) NOT NULL,
  receipt_key VARCHAR(128) NOT NULL COMMENT '幂等键',
  payment_no VARCHAR(40) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_receipt (provider, receipt_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='支付回调回执';

-- Webhook 收件箱（先落库再异步消费）
CREATE TABLE IF NOT EXISTS webhook_inbox (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  channel VARCHAR(30) NOT NULL COMMENT 'WECHAT_PAY/ALIPAY/MEITUAN/ELEME',
  event_id VARCHAR(128) NOT NULL COMMENT '平台事件唯一 ID',
  event_type VARCHAR(50) DEFAULT NULL,
  payload MEDIUMTEXT NOT NULL,
  headers_json TEXT DEFAULT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PROCESSING/DONE/FAILED',
  error_message VARCHAR(500) DEFAULT NULL,
  received_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  processed_at DATETIME(3) DEFAULT NULL,
  UNIQUE KEY uk_webhook_event (channel, event_id),
  INDEX idx_wi_status (status, received_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Webhook 收件箱';

-- ============================================================================
-- 六、外卖（美团 / 饿了么）
-- ============================================================================

CREATE TABLE IF NOT EXISTS delivery_product_maps (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  store_id BIGINT UNSIGNED NOT NULL,
  platform VARCHAR(20) NOT NULL COMMENT 'MEITUAN/ELEME',
  platform_sku_id VARCHAR(64) NOT NULL COMMENT '平台 SKU ID',
  platform_spu_code VARCHAR(64) DEFAULT NULL COMMENT '平台 SPU / app_food_code',
  app_food_code VARCHAR(64) DEFAULT NULL COMMENT '本系统对外编码',
  option_signature VARCHAR(128) DEFAULT NULL COMMENT '规格组合签名',
  platform_sku_name VARCHAR(100) DEFAULT NULL,
  product_id BIGINT UNSIGNED NOT NULL,
  option_map_json JSON DEFAULT NULL COMMENT '平台规格→本系统 option 映射',
  status TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_platform_sku (store_id, platform, platform_sku_id),
  INDEX idx_dpm_product (product_id),
  CONSTRAINT fk_dpm_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_dpm_product FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='外卖商品映射';

CREATE TABLE IF NOT EXISTS delivery_orders (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  order_id BIGINT UNSIGNED NOT NULL UNIQUE COMMENT '关联统一订单',
  platform VARCHAR(20) NOT NULL COMMENT 'MEITUAN/ELEME',
  platform_order_id VARCHAR(64) NOT NULL,
  platform_day_seq VARCHAR(32) DEFAULT NULL COMMENT '当日流水号/#号牌',
  delivery_status VARCHAR(30) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/ACCEPTED/READY/DELIVERING/DONE/CANCELLED',
  recipient_name VARCHAR(50) DEFAULT NULL,
  recipient_phone VARCHAR(20) DEFAULT NULL COMMENT '隐私号',
  recipient_address VARCHAR(500) DEFAULT NULL,
  rider_name VARCHAR(50) DEFAULT NULL,
  rider_phone VARCHAR(20) DEFAULT NULL,
  expected_delivery_at DATETIME(3) DEFAULT NULL,
  platform_fee DECIMAL(10,2) DEFAULT 0.00,
  packing_fee DECIMAL(10,2) DEFAULT 0.00,
  delivery_fee DECIMAL(10,2) DEFAULT 0.00,
  logistics_status VARCHAR(30) DEFAULT NULL COMMENT '物流状态',
  logistics_code VARCHAR(50) DEFAULT NULL,
  refund_status VARCHAR(30) DEFAULT 'NONE' COMMENT 'NONE/PENDING/APPROVED/REJECTED',
  urge_count INT NOT NULL DEFAULT 0 COMMENT '催单次数',
  completed_at DATETIME(3) DEFAULT NULL,
  raw_payload JSON DEFAULT NULL,
  last_sync_at DATETIME(3) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_platform_order (platform, platform_order_id),
  INDEX idx_do_status (delivery_status, created_at),
  CONSTRAINT fk_do_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='外卖订单扩展';

CREATE TABLE IF NOT EXISTS platform_store_bindings (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  store_id BIGINT UNSIGNED NOT NULL,
  platform VARCHAR(20) NOT NULL COMMENT 'MEITUAN/ELEME',
  platform_shop_id VARCHAR(64) NOT NULL COMMENT '平台门店 ID',
  platform_shop_name VARCHAR(100) DEFAULT NULL,
  access_token TEXT DEFAULT NULL,
  refresh_token TEXT DEFAULT NULL,
  token_expires_at DATETIME(3) DEFAULT NULL,
  extra_config JSON DEFAULT NULL,
  status TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_store_platform_shop (store_id, platform, platform_shop_id),
  CONSTRAINT fk_psb_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='外卖平台门店绑定';

CREATE TABLE IF NOT EXISTS delivery_refunds (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  delivery_order_id BIGINT UNSIGNED NOT NULL,
  order_id BIGINT UNSIGNED NOT NULL,
  platform VARCHAR(20) NOT NULL,
  platform_refund_id VARCHAR(64) DEFAULT NULL,
  refund_type VARCHAR(30) NOT NULL DEFAULT 'FULL' COMMENT 'FULL/PARTIAL',
  amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  reason VARCHAR(500) DEFAULT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/APPROVED/REJECTED',
  raw_payload JSON DEFAULT NULL,
  handled_at DATETIME(3) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_dr_order (order_id),
  CONSTRAINT fk_dr_do FOREIGN KEY (delivery_order_id) REFERENCES delivery_orders(id) ON DELETE CASCADE,
  CONSTRAINT fk_dr_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='外卖退款单';

CREATE TABLE IF NOT EXISTS delivery_callback_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  platform VARCHAR(20) NOT NULL,
  event_type VARCHAR(50) NOT NULL,
  event_id VARCHAR(128) DEFAULT NULL COMMENT '平台事件 ID（幂等）',
  platform_order_id VARCHAR(64) DEFAULT NULL,
  order_id BIGINT UNSIGNED DEFAULT NULL,
  payload JSON DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_dcl_event (platform, event_id),
  INDEX idx_dcl_platform_order (platform, platform_order_id, created_at),
  INDEX idx_dcl_event (event_type, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='外卖回调审计日志';

-- ============================================================================
-- 七、摄像头 / 客流 / 人脸迎宾
-- ============================================================================

CREATE TABLE IF NOT EXISTS cameras (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  store_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(100) NOT NULL,
  camera_type VARCHAR(20) NOT NULL COMMENT 'USB/RTSP/HTTP',
  source_url VARCHAR(500) NOT NULL COMMENT 'USB 设备号或 RTSP 地址（密码脱敏存储）',
  username VARCHAR(100) DEFAULT NULL,
  password_encrypted VARCHAR(500) DEFAULT NULL COMMENT '加密后的密码',
  status VARCHAR(20) NOT NULL DEFAULT 'OFFLINE' COMMENT 'ONLINE/OFFLINE/RECONNECTING',
  fps INT NOT NULL DEFAULT 25 COMMENT '采集帧率',
  detect_fps INT NOT NULL DEFAULT 3 COMMENT '识别采样帧率',
  last_online_at DATETIME(3) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_cameras_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='摄像头';

CREATE TABLE IF NOT EXISTS visitor_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  visitor_no VARCHAR(64) NOT NULL COMMENT '匿名访客号',
  camera_id BIGINT UNSIGNED NOT NULL,
  event_type VARCHAR(30) NOT NULL COMMENT 'ENTER/EXIT/PASS',
  event_time DATETIME(3) NOT NULL,
  snapshot_path VARCHAR(500) DEFAULT NULL,
  expires_at DATETIME(3) NOT NULL COMMENT '到期自动清理',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_ve_camera_time (camera_id, event_time),
  INDEX idx_ve_expires (expires_at),
  CONSTRAINT fk_ve_camera FOREIGN KEY (camera_id) REFERENCES cameras(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='匿名客流事件';

CREATE TABLE IF NOT EXISTS face_profiles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id BIGINT UNSIGNED NOT NULL,
  feature_data BLOB NOT NULL COMMENT '加密特征向量',
  feature_version VARCHAR(20) NOT NULL DEFAULT 'v1',
  consent_status VARCHAR(20) NOT NULL DEFAULT 'AUTHORIZED' COMMENT 'AUTHORIZED/REVOKED',
  consent_at DATETIME(3) NOT NULL,
  revoked_at DATETIME(3) DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  INDEX idx_face_customer (customer_id, consent_status),
  CONSTRAINT fk_face_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='授权人脸档案';

CREATE TABLE IF NOT EXISTS welcome_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id BIGINT UNSIGNED NOT NULL,
  camera_id BIGINT UNSIGNED DEFAULT NULL,
  welcome_text VARCHAR(500) NOT NULL COMMENT '欢迎语（含偏好推荐）',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_welcome_customer (customer_id, created_at),
  CONSTRAINT fk_welcome_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='迎宾日志';

-- ============================================================================
-- 八、全局系统配置（可在线改的业务参数）
-- ============================================================================

CREATE TABLE IF NOT EXISTS system_settings (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  setting_key VARCHAR(100) NOT NULL UNIQUE COMMENT '配置键',
  setting_value TEXT NOT NULL COMMENT '配置值',
  description VARCHAR(255) DEFAULT NULL,
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统业务参数';

SET FOREIGN_KEY_CHECKS = 1;

-- 完成：请继续执行 002_seed_test.sql 导入测试数据

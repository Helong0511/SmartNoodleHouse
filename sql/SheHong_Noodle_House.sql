-- =============================================================================
-- SmartNoodleHouse 唯一数据库真源（MySQL 8.0+）
-- 包含：建库与授权、完整表结构、生产基础数据。执行会删除并重建 smart_noodle_house。
-- 所有表/字段/类型/索引/约束/基础数据变更只维护本文件。
-- =============================================================================

-- BEGIN DATABASE BOOTSTRAP
-- ============================================================================
-- 建库（可选，需 MySQL root）
-- ============================================================================
-- 本文件已包含建库、结构与生产基础数据；执行后运行 python scripts/init_admin.py 设置强密码。
--
-- 应用账号请与 .env 中 DATABASE_URL 一致，示例：
--   mysql+aiomysql://remote:强密码@127.0.0.1:3306/smart_noodle_house
-- ============================================================================

-- 删除数据库
DROP DATABASE IF EXISTS smart_noodle_house;

CREATE DATABASE IF NOT EXISTS smart_noodle_house
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- 部署前替换下方占位符；禁止把真实数据库密码提交到版本库。
CREATE USER IF NOT EXISTS 'remote'@'localhost' IDENTIFIED BY 'CHANGE_ME_BEFORE_PRODUCTION';
GRANT ALL PRIVILEGES ON smart_noodle_house.* TO 'remote'@'localhost';
FLUSH PRIVILEGES;
-- ============================================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE DATABASE IF NOT EXISTS smart_noodle_house
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE smart_noodle_house;

-- ============================================================================
-- 一、系统用户与 RBAC（角色 / 权限 / 员工）
-- 用途：后台管理端登录、接口鉴权、操作审计
-- ============================================================================

-- 后台角色定义（如超管、店长、收银、后厨、运营）
CREATE TABLE IF NOT EXISTS roles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  code VARCHAR(50) NOT NULL UNIQUE COMMENT '角色编码：SUPER_ADMIN=超管 | STORE_ADMIN=店长 | CASHIER=收银 | KITCHEN=后厨 | OPERATOR=运营',
  name VARCHAR(100) NOT NULL COMMENT '角色显示名称',
  description VARCHAR(255) DEFAULT NULL COMMENT '角色职责说明',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='后台角色表';

-- 细粒度权限点（与 API require_permission 对应）
CREATE TABLE IF NOT EXISTS permissions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  code VARCHAR(100) NOT NULL UNIQUE COMMENT '权限码，如 product:read、order:update、welcome:manage',
  name VARCHAR(100) NOT NULL COMMENT '权限名称（中文展示）',
  module VARCHAR(50) NOT NULL COMMENT '所属模块：product/order/payment/customer 等',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='权限点表';

-- 角色与权限多对多关联
CREATE TABLE IF NOT EXISTS role_permissions (
  role_id BIGINT UNSIGNED NOT NULL COMMENT '角色 ID → roles.id',
  permission_id BIGINT UNSIGNED NOT NULL COMMENT '权限 ID → permissions.id',
  PRIMARY KEY (role_id, permission_id),
  CONSTRAINT fk_rp_role FOREIGN KEY (role_id) REFERENCES roles(id),
  CONSTRAINT fk_rp_perm FOREIGN KEY (permission_id) REFERENCES permissions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色-权限关联表';

-- 后台员工账号（JWT typ=staff）
CREATE TABLE IF NOT EXISTS users (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  username VARCHAR(50) NOT NULL UNIQUE COMMENT '登录用户名，全局唯一',
  password_hash VARCHAR(255) NOT NULL COMMENT 'bcrypt 等算法哈希后的密码，明文不入库',
  display_name VARCHAR(100) NOT NULL COMMENT '界面显示姓名',
  role_id BIGINT UNSIGNED NOT NULL COMMENT '角色 ID → roles.id',
  store_id BIGINT UNSIGNED DEFAULT NULL COMMENT '绑定门店 ID；NULL=超管可跨店，非空=仅能操作该店数据',
  token_version INT NOT NULL DEFAULT 0 COMMENT 'JWT 版本号；改密/踢下线时递增，使旧令牌失效',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '账号状态：1=启用 0=停用',
  last_login_at DATETIME(3) DEFAULT NULL COMMENT '最近登录时间',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  INDEX idx_users_store (store_id),
  CONSTRAINT fk_users_role FOREIGN KEY (role_id) REFERENCES roles(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='后台员工用户表';

-- 敏感操作审计日志（谁在何时对什么对象做了什么）
CREATE TABLE IF NOT EXISTS operation_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  user_id BIGINT UNSIGNED DEFAULT NULL COMMENT '操作人 → users.id；系统自动任务可为 NULL',
  action VARCHAR(100) NOT NULL COMMENT '动作编码，如 order.cancel、product.update',
  module VARCHAR(50) NOT NULL COMMENT '业务模块',
  target_type VARCHAR(50) DEFAULT NULL COMMENT '目标实体类型，如 Order、Product',
  target_id BIGINT UNSIGNED DEFAULT NULL COMMENT '目标实体主键',
  ip VARCHAR(45) DEFAULT NULL COMMENT '客户端 IP（支持 IPv6）',
  user_agent VARCHAR(500) DEFAULT NULL COMMENT '浏览器/客户端 UA',
  request_id VARCHAR(64) DEFAULT NULL COMMENT '请求链路 ID，便于与日志系统关联',
  detail JSON DEFAULT NULL COMMENT '变更详情、请求参数摘要等 JSON',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '记录时间',
  INDEX idx_op_logs_user (user_id, created_at),
  INDEX idx_op_logs_module (module, created_at),
  INDEX idx_op_logs_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='后台操作审计日志';

-- ============================================================================
-- 二、门店与桌台
-- 用途：多店经营、扫码绑桌、门店级参数
-- ============================================================================

CREATE TABLE IF NOT EXISTS `stores` (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_no VARCHAR(32) NOT NULL UNIQUE COMMENT '门店编号（写入业务单号前缀，如 TH01）',
  name VARCHAR(100) NOT NULL COMMENT '门店名称',
  address VARCHAR(255) DEFAULT NULL COMMENT '门店地址',
  phone VARCHAR(20) DEFAULT NULL COMMENT '门店联系电话',
  latitude DECIMAL(10,7) DEFAULT NULL COMMENT '纬度（天气口味加权）',
  longitude DECIMAL(10,7) DEFAULT NULL COMMENT '经度（天气口味加权）',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '营业状态：1=营业中 0=停业/禁用',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='门店表';

-- 堂食桌台；qr_token 用于扫码点餐，不可伪造桌号
CREATE TABLE IF NOT EXISTS `tables` (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '所属门店 → stores.id',
  table_no VARCHAR(20) NOT NULL COMMENT '桌号编码（店内唯一，如 01、A12）',
  table_name VARCHAR(50) NOT NULL COMMENT '顾客端展示名（如 1号桌、包厢A）',
  qr_token VARCHAR(64) NOT NULL UNIQUE COMMENT '扫码令牌；正式环境随机串，开发环境可用桌号作 token',
  wechat_qr_url VARCHAR(500) DEFAULT NULL COMMENT '微信小程序码落地路径或参数',
  alipay_qr_url VARCHAR(500) DEFAULT NULL COMMENT '支付宝小程序码落地路径或参数',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '桌台状态：1=启用 0=禁用',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  UNIQUE KEY uk_store_table (store_id, table_no),
  INDEX idx_tables_token (qr_token),
  CONSTRAINT fk_tables_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='堂食桌台表';

-- 门店级 KV 配置（打包费开关、营业时间等可扩展项）
CREATE TABLE IF NOT EXISTS store_settings (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  setting_key VARCHAR(100) NOT NULL COMMENT '参数键，如 packing_fee_enabled',
  setting_value VARCHAR(500) NOT NULL COMMENT '参数值（字符串存储，由业务解析类型）',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '最后修改时间',
  UNIQUE KEY uk_store_setting (store_id, setting_key),
  CONSTRAINT fk_ss_store FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='门店业务参数表';

-- ============================================================================
-- 三、顾客 / 会员 / 第三方账号 / 营销券
-- 用途：C 端身份、会员等级与积分、微信/支付宝绑定、口味学习、优惠券/代金券
-- ============================================================================

-- 会员等级（积分达标自动升级，享 discount_rate 折扣）
CREATE TABLE IF NOT EXISTS member_levels (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  name VARCHAR(50) NOT NULL COMMENT '等级名称，如 普通会员、银卡会员',
  min_points INT NOT NULL DEFAULT 0 COMMENT '升级所需累计积分下限',
  discount_rate DECIMAL(5,4) NOT NULL DEFAULT 1.0000 COMMENT '折扣率：1.0000=无折扣，0.9000=九折',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='会员等级定义表';

-- 券模板（优惠券 + 代金券共用；coupon_type=VOUCHER 时代金券）
CREATE TABLE IF NOT EXISTS coupon_templates (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED DEFAULT NULL COMMENT 'NULL=总部全店模板；非空=仅该门店 → stores.id',
  name VARCHAR(100) NOT NULL COMMENT '券名称（展示用）',
  coupon_type VARCHAR(20) NOT NULL DEFAULT 'FIXED' COMMENT 'FIXED=满减元 | PERCENT=折扣比例 | VOUCHER=代金券抵扣',
  discount_value DECIMAL(10,4) NOT NULL COMMENT 'FIXED/VOUCHER=减免金额；PERCENT=折扣比例如0.90表示九折',
  min_order_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '使用门槛（按会员折后商品小计计）',
  valid_days INT NOT NULL DEFAULT 30 COMMENT '领取/发放后有效天数',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '模板状态：1=启用 0=停用',
  total_quota INT DEFAULT NULL COMMENT 'P0-6: 发放总量上限；NULL=不限',
  remaining INT DEFAULT NULL COMMENT 'P0-6: 剩余可发量；NULL=不限',
  store_whitelist JSON DEFAULT NULL COMMENT 'P2-4: 总部模板可见门店白名单；NULL/空数组=全部门店',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  INDEX idx_coupon_tmpl_store (store_id),
  CONSTRAINT fk_coupon_tmpl_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='优惠券/代金券模板表';

-- 顾客主档（小程序/H5 登录主体）
CREATE TABLE IF NOT EXISTS customers (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  customer_no VARCHAR(40) NOT NULL UNIQUE COMMENT '顾客编号（业务可读单号）',
  display_name VARCHAR(100) DEFAULT NULL COMMENT '展示名（可与昵称相同）',
  nickname VARCHAR(100) DEFAULT NULL COMMENT '收银台/后台展示昵称（小程序微信/支付宝各存在对应账号表）',
  avatar_url VARCHAR(500) DEFAULT NULL COMMENT '收银台/后台展示头像（小程序按渠道账号分存）',
  phone VARCHAR(11) DEFAULT NULL COMMENT '手机号；绑定后可合并历史账号',
  member_level_id BIGINT UNSIGNED DEFAULT NULL COMMENT '当前会员等级 → member_levels.id',
  points INT NOT NULL DEFAULT 0 COMMENT '累计积分；实付1元=1分，达门槛自动升级',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '顾客状态：1=正常 0=禁用（兼容字段；以 account_status 为准）',
  member_status VARCHAR(20) NOT NULL DEFAULT 'GUEST' COMMENT 'GUEST=游客（可点餐） MEMBER=已主动开通会员',
  account_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/DISABLED/DELETED',
  profile_status VARCHAR(20) NOT NULL DEFAULT 'EMPTY' COMMENT 'EMPTY/PARTIAL/COMPLETED 资料完善度（≠会员）',
  phone_verified TINYINT NOT NULL DEFAULT 0 COMMENT '手机号是否已验证',
  member_activated_at DATETIME(3) DEFAULT NULL COMMENT '首次主动开通会员时间（≠首次扫码）',
  first_seen_at DATETIME(3) DEFAULT NULL COMMENT '首次扫码/识别时间（≠会员激活）',
  first_seen_store_id BIGINT UNSIGNED DEFAULT NULL COMMENT '首次扫码门店 → stores.id',
  first_seen_table_id BIGINT UNSIGNED DEFAULT NULL COMMENT '首次扫码桌台 → tables.id',
  token_version INT NOT NULL DEFAULT 0 COMMENT 'JWT 版本号；禁用/合并账号时递增，使旧令牌失效',
  registered_store_id BIGINT UNSIGNED DEFAULT NULL COMMENT '会员激活时门店 → stores.id；只记第一次',
  registered_table_id BIGINT UNSIGNED DEFAULT NULL COMMENT '会员激活时桌台 → tables.id；只记第一次',
  last_order_at DATETIME(3) DEFAULT NULL COMMENT '最近下单时间',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '首次建档时间（游客即可）',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  INDEX idx_customers_phone (phone),
  INDEX idx_customers_last_order (last_order_at),
  INDEX idx_customers_reg_store (registered_store_id),
  INDEX idx_customers_member_status (member_status),
  CONSTRAINT fk_customers_level FOREIGN KEY (member_level_id) REFERENCES member_levels(id),
  CONSTRAINT fk_customers_reg_store FOREIGN KEY (registered_store_id) REFERENCES stores(id),
  CONSTRAINT fk_customers_reg_table FOREIGN KEY (registered_table_id) REFERENCES tables(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='顾客主表（游客/会员统一身份）';

-- 用户持有的券实例（同模板可多张；按张核销）
CREATE TABLE IF NOT EXISTS customer_coupons (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  customer_id BIGINT UNSIGNED NOT NULL COMMENT '持有人 → customers.id',
  template_id BIGINT UNSIGNED NOT NULL COMMENT '券模板 → coupon_templates.id',
  coupon_no VARCHAR(40) NOT NULL UNIQUE COMMENT '券实例编号（全局唯一）',
  status VARCHAR(20) NOT NULL DEFAULT 'AVAILABLE' COMMENT 'AVAILABLE=可用 | RESERVED=下单预占 | USED=已使用 | EXPIRED=已过期',
  status_reason VARCHAR(40) DEFAULT NULL COMMENT '状态原因：merge_dup=合并冲突作废 等',
  obtained_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '领取/发放时间',
  expires_at DATETIME(3) NOT NULL COMMENT '过期时间',
  used_order_id BIGINT UNSIGNED DEFAULT NULL COMMENT '核销订单 → orders.id',
  used_at DATETIME(3) DEFAULT NULL COMMENT '核销时间',
  client_request_id VARCHAR(128) DEFAULT NULL COMMENT 'P0-6: 客户端幂等键；同键重复发券只成功一次',
  issued_by_user_id BIGINT UNSIGNED DEFAULT NULL COMMENT '发券人 user_id',
  issued_at_store_id BIGINT UNSIGNED DEFAULT NULL COMMENT '发券门店；NULL=不限门店使用',
  UNIQUE INDEX uq_cc_client_request (client_request_id),
  INDEX idx_cc_customer_status (customer_id, status, expires_at),
  INDEX idx_cc_customer_template (customer_id, template_id),
  INDEX idx_cc_issued_store (issued_at_store_id),
  INDEX idx_cc_issued_by (issued_by_user_id),
  CONSTRAINT fk_cc_customer FOREIGN KEY (customer_id) REFERENCES customers(id),
  CONSTRAINT fk_cc_template FOREIGN KEY (template_id) REFERENCES coupon_templates(id),
  CONSTRAINT fk_cc_issued_user FOREIGN KEY (issued_by_user_id) REFERENCES users(id),
  CONSTRAINT fk_cc_issued_store FOREIGN KEY (issued_at_store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户优惠券/代金券实例表';

-- 微信小程序 openid 与顾客绑定（支付、订阅消息依赖 openid）
CREATE TABLE IF NOT EXISTS customer_wechat_accounts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  customer_id BIGINT UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  openid VARCHAR(64) NOT NULL UNIQUE COMMENT '微信 openid，小程序内唯一',
  unionid VARCHAR(64) DEFAULT NULL COMMENT '微信 unionid（同主体多应用打通）',
  profile_completed TINYINT NOT NULL DEFAULT 0 COMMENT '本渠道是否完善过昵称/头像资料（≠member_status）',
  nickname VARCHAR(100) DEFAULT NULL COMMENT '本渠道展示昵称（与支付宝互不覆盖）',
  avatar_url VARCHAR(500) DEFAULT NULL COMMENT '本渠道展示头像（与支付宝互不覆盖）',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '首次绑定时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  INDEX idx_wechat_unionid (unionid),
  CONSTRAINT fk_cwa_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='微信账号绑定表';

-- 支付宝小程序用户 ID 与顾客绑定
CREATE TABLE IF NOT EXISTS customer_alipay_accounts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  customer_id BIGINT UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  alipay_user_id VARCHAR(64) NOT NULL UNIQUE COMMENT '支付宝 user_id',
  alipay_union_id VARCHAR(64) DEFAULT NULL COMMENT '支付宝 union_id（如有）',
  profile_completed TINYINT NOT NULL DEFAULT 0 COMMENT '本渠道是否完善过昵称/头像资料（≠member_status）',
  nickname VARCHAR(100) DEFAULT NULL COMMENT '本渠道展示昵称（与微信互不覆盖）',
  avatar_url VARCHAR(500) DEFAULT NULL COMMENT '本渠道展示头像（与微信互不覆盖）',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '首次绑定时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  CONSTRAINT fk_caa_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='支付宝账号绑定表';

-- 服务端验证过的稳定身份；昵称/头像/手输手机号不得写入
CREATE TABLE IF NOT EXISTS customer_identities (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  customer_id BIGINT UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  provider VARCHAR(32) NOT NULL COMMENT 'WECHAT_OPENID/WECHAT_UNIONID/ALIPAY_USER_ID/PHONE',
  provider_subject VARCHAR(128) NOT NULL COMMENT '渠道稳定身份；日志中必须脱敏',
  verified TINYINT NOT NULL DEFAULT 1 COMMENT '是否经渠道服务端验证',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_customer_identity_provider_subject (provider, provider_subject),
  INDEX idx_customer_identity_customer (customer_id, provider),
  CONSTRAINT fk_customer_identity_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='统一顾客稳定身份绑定';

-- 就餐会话：扫码绑桌建立，过期后不再沿用旧桌台
CREATE TABLE IF NOT EXISTS dining_sessions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  customer_id BIGINT UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  table_id BIGINT UNSIGNED NOT NULL COMMENT '桌台 → tables.id',
  table_token VARCHAR(64) DEFAULT NULL COMMENT '绑定时桌台二维码 token 快照',
  status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/EXPIRED/COMPLETED/CANCELLED',
  started_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '会话开始',
  last_active_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '最近活跃',
  expires_at DATETIME(3) NOT NULL COMMENT '过期时间',
  ended_at DATETIME(3) DEFAULT NULL COMMENT '结束时间',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  INDEX idx_ds_customer_status (customer_id, status, expires_at),
  INDEX idx_ds_store_table (store_id, table_id, status),
  CONSTRAINT fk_ds_customer FOREIGN KEY (customer_id) REFERENCES customers(id),
  CONSTRAINT fk_ds_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_ds_table FOREIGN KEY (table_id) REFERENCES tables(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='就餐会话（扫码绑桌，非永久桌台归属）';

-- 智能口味：按历史订单累计规格偏好，点餐时预选
CREATE TABLE IF NOT EXISTS customer_preferences (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  customer_id BIGINT UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  category VARCHAR(50) NOT NULL COMMENT '规格组名称快照（如 辣度、份量）',
  option_code VARCHAR(50) NOT NULL COMMENT '选项编码快照',
  option_name VARCHAR(50) DEFAULT NULL COMMENT '选项名称快照（展示用）',
  preference_score DECIMAL(5,4) NOT NULL DEFAULT 0.0000 COMMENT '偏好得分，越高越常点',
  order_count INT NOT NULL DEFAULT 0 COMMENT '累计点选次数',
  last_used_at DATETIME(3) DEFAULT NULL COMMENT '最近一次点选时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '统计更新时间',
  UNIQUE KEY uk_cust_pref (customer_id, category, option_code),
  INDEX idx_pref_customer (customer_id, category),
  INDEX idx_customer_prefs_score (customer_id, preference_score DESC),
  CONSTRAINT fk_pref_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='顾客口味偏好表（全门店汇总，不按店隔离）';

-- ============================================================================
-- 四、商品 / 分类 / 规格
-- 用途：菜单、加料规格、分店库存覆盖
-- ============================================================================

CREATE TABLE IF NOT EXISTS product_categories (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '所属门店 → stores.id',
  category_no VARCHAR(64) NOT NULL COMMENT '分类编号：{store_no}-拼音逻辑，如 SH01-Mian',
  name VARCHAR(100) NOT NULL COMMENT '分类名称（如 招牌面、小吃）',
  description VARCHAR(255) DEFAULT NULL COMMENT '分类描述',
  sort_order INT NOT NULL DEFAULT 0 COMMENT '排序权重，越小越靠前',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1=展示 0=隐藏',
  require_category_ids JSON DEFAULT NULL COMMENT '须先点过的分类 id 列表（OR：满足任一即可）；NULL/空=无限制。例：加料依赖面',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  UNIQUE KEY uk_cat_store_no (store_id, category_no),
  INDEX idx_cat_store (store_id, sort_order, status),
  CONSTRAINT fk_cat_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品分类表（按门店）';

CREATE TABLE IF NOT EXISTS category_sort_schedules (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  name VARCHAR(100) NOT NULL COMMENT '方案名（如 早餐优先）',
  start_time TIME NOT NULL COMMENT '开始时间（含）',
  end_time TIME NOT NULL COMMENT '结束时间（不含）；若 start>end 表示跨午夜',
  priority INT NOT NULL DEFAULT 0 COMMENT '重叠时取更大优先级',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  INDEX idx_cat_sched_store (store_id, status, priority),
  CONSTRAINT fk_cat_sched_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='分类分时段排序方案';

CREATE TABLE IF NOT EXISTS category_sort_schedule_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  schedule_id BIGINT UNSIGNED NOT NULL COMMENT '方案 → category_sort_schedules.id',
  category_id BIGINT UNSIGNED NOT NULL COMMENT '分类 → product_categories.id',
  sort_order INT NOT NULL DEFAULT 0 COMMENT '时段内排序，越小越靠前',
  UNIQUE KEY uk_sched_cat (schedule_id, category_id),
  INDEX idx_sched_item_order (schedule_id, sort_order),
  CONSTRAINT fk_sched_item_sched FOREIGN KEY (schedule_id) REFERENCES category_sort_schedules(id) ON DELETE CASCADE,
  CONSTRAINT fk_sched_item_cat FOREIGN KEY (category_id) REFERENCES product_categories(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='时段方案内分类排序';

CREATE TABLE IF NOT EXISTS products (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '所属门店 → stores.id',
  category_id BIGINT UNSIGNED NOT NULL COMMENT '分类 → product_categories.id',
  product_no VARCHAR(64) NOT NULL COMMENT '店内商品编号：{store_no|0000}-分类逻辑-商品逻辑，如 SH01-Mian-NiuRouMian',
  name VARCHAR(100) NOT NULL COMMENT '商品名称',
  description TEXT DEFAULT NULL COMMENT '商品描述/卖点',
  main_image VARCHAR(500) DEFAULT NULL COMMENT '主图相对或绝对路径',
  price DECIMAL(10,2) NOT NULL COMMENT '基础售价（不含规格加价）',
  original_price DECIMAL(10,2) DEFAULT NULL COMMENT '划线原价（可选）',
  stock INT NOT NULL DEFAULT 999 COMMENT '默认库存；999 表示不限量',
  box_price DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '单份餐盒/打包费',
  min_order_count INT NOT NULL DEFAULT 1 COMMENT '起售份数',
  require_category_ids JSON DEFAULT NULL COMMENT '须先点过的分类 id（OR）；NULL=继承所属分类默认',
  require_product_ids JSON DEFAULT NULL COMMENT '须先点过的指定商品 id（OR）；与分类依赖合并 OR',
  status VARCHAR(20) NOT NULL DEFAULT 'ON_SALE' COMMENT 'ON_SALE=在售 | OFF_SALE=停售 | SOLD_OUT=售罄',
  is_recommended TINYINT NOT NULL DEFAULT 0 COMMENT '是否推荐：1=是 0=否',
  is_hot TINYINT NOT NULL DEFAULT 0 COMMENT '是否热销标签：1=是 0=否',
  no_member_discount TINYINT NOT NULL DEFAULT 0 COMMENT '特价/不参与会员折扣：1=是 0=否',
  no_cooking TINYINT NOT NULL DEFAULT 0 COMMENT '免烹饪/现成品（卤味等）：1=KDS可直出餐 0=需正常制作',
  sort_order INT NOT NULL DEFAULT 0 COMMENT '分类内排序',
  sales_count INT NOT NULL DEFAULT 0 COMMENT '累计销量（冗余统计）',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  UNIQUE KEY uk_products_store_no (store_id, product_no),
  INDEX idx_products_store (store_id, status, sort_order),
  INDEX idx_products_category (category_id, status, sort_order),
  CONSTRAINT fk_products_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_products_category FOREIGN KEY (category_id) REFERENCES product_categories(id),
  CONSTRAINT chk_products_stock_nonneg CHECK (stock >= 0),
  CONSTRAINT chk_products_price_pos CHECK (price > 0),
  CONSTRAINT chk_products_orig_price CHECK (original_price IS NULL OR original_price >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品表（按门店）';

CREATE TABLE IF NOT EXISTS product_images (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品 → products.id',
  image_url VARCHAR(500) NOT NULL COMMENT '图片 URL',
  image_type VARCHAR(20) NOT NULL DEFAULT 'DETAIL' COMMENT 'MAIN=主图 | DETAIL=详情图 | OPTION=规格示意图',
  sort_order INT NOT NULL DEFAULT 0 COMMENT '同类型内排序',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  INDEX idx_pi_product (product_id, sort_order),
  CONSTRAINT fk_pi_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品图片表';

-- 分店库存/停售覆盖（未配置则沿用 products.stock / status）
CREATE TABLE IF NOT EXISTS store_product_stocks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品 → products.id',
  stock INT NOT NULL DEFAULT 999 COMMENT '本店库存覆盖；999=不限',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '本店售卖状态：1=在售 0=本店停售',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  UNIQUE KEY uk_store_product (store_id, product_id),
  INDEX idx_sps_product (product_id),
  CONSTRAINT fk_sps_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_sps_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
  CONSTRAINT chk_sps_stock_nonneg CHECK (stock >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='分店商品库存覆盖表';

-- 规格组（全局字典，如 份量、辣度、加料）
CREATE TABLE IF NOT EXISTS option_groups (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED DEFAULT NULL COMMENT 'NULL=总部共享；非空=仅该门店',
  name VARCHAR(50) NOT NULL COMMENT '规格组显示名',
  code VARCHAR(64) NOT NULL COMMENT '规格组编码：SH01-Liang 或 SH01-Liang',
  selection_type VARCHAR(20) NOT NULL DEFAULT 'SINGLE' COMMENT 'SINGLE=单选 | MULTIPLE=多选',
  is_required TINYINT NOT NULL DEFAULT 1 COMMENT '是否必选：1=必选 0=可选',
  sort_order INT NOT NULL DEFAULT 0 COMMENT '展示排序',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  store_scope BIGINT UNSIGNED AS (IFNULL(store_id, 0)) STORED COMMENT '唯一约束用：NULL store→0',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  UNIQUE KEY uk_og_store_code (store_scope, code),
  CONSTRAINT fk_og_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品规格组表';

-- 规格选项（隶属于某规格组）
CREATE TABLE IF NOT EXISTS options (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  group_id BIGINT UNSIGNED NOT NULL COMMENT '规格组 → option_groups.id',
  name VARCHAR(50) NOT NULL COMMENT '选项显示名，如 大份、微辣',
  code VARCHAR(80) NOT NULL COMMENT '选项编码：SH01-Liang-XiaoWan 或 SH01-Liang-XiaoWan',
  extra_price DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '相对基础价的加价',
  sort_order INT NOT NULL DEFAULT 0 COMMENT '组内排序',
  is_default TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否默认选项（同组单选仅一个；菜单+一键加购）',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  UNIQUE KEY uk_group_code (group_id, code),
  CONSTRAINT fk_options_group FOREIGN KEY (group_id) REFERENCES option_groups(id),
  CONSTRAINT chk_options_extra_price CHECK (extra_price >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='规格选项表';

-- 商品与规格组多对多绑定
CREATE TABLE IF NOT EXISTS product_option_groups (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品 → products.id',
  group_id BIGINT UNSIGNED NOT NULL COMMENT '规格组 → option_groups.id',
  sort_order INT NOT NULL DEFAULT 0 COMMENT '该商品下规格组展示顺序',
  UNIQUE KEY uk_product_group (product_id, group_id),
  CONSTRAINT fk_pog_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
  CONSTRAINT fk_pog_group FOREIGN KEY (group_id) REFERENCES option_groups(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品-规格组关联表';

-- ============================================================================
-- 五、订单 / 支付 / 退款
-- 用途：堂食/打包/外卖统一订单、支付流水、退款与回调幂等
-- ============================================================================

CREATE TABLE IF NOT EXISTS orders (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  order_no VARCHAR(40) NOT NULL UNIQUE COMMENT '业务订单号（含门店/桌位/时间等可读信息）',
  customer_id BIGINT UNSIGNED DEFAULT NULL COMMENT '下单顾客 → customers.id；匿名单可为空',
  table_id BIGINT UNSIGNED DEFAULT NULL COMMENT '堂食桌台 → tables.id；外卖/打包可空',
  dining_session_id BIGINT UNSIGNED DEFAULT NULL COMMENT '就餐会话 → dining_sessions.id',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING_PAYMENT' COMMENT '订单履约状态，见文件头枚举',
  dining_type VARCHAR(20) NOT NULL DEFAULT 'DINE_IN' COMMENT 'DINE_IN=堂食 | TAKEAWAY=打包外带',
  subtotal DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '商品折前小计（含规格加价）',
  discount_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '会员等级折扣减免金额',
  customer_coupon_id BIGINT UNSIGNED DEFAULT NULL COMMENT '使用的优惠券 → customer_coupons.id',
  coupon_discount DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '优惠券减免金额（非代金券）',
  customer_voucher_id BIGINT UNSIGNED DEFAULT NULL COMMENT '使用的代金券 → customer_coupons.id',
  voucher_discount DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '代金券抵扣金额',
  packing_fee DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '打包/餐盒费合计',
  total_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '应付总额（见文件头计算公式）',
  payment_status VARCHAR(20) NOT NULL DEFAULT 'UNPAID' COMMENT 'UNPAID/PARTIAL_PAID/PAID/PARTIAL_REFUND/REFUNDING/REFUNDED',
  payment_method VARCHAR(20) DEFAULT NULL COMMENT '支付方式摘要：WECHAT/ALIPAY/CASH/MIXED',
  channel VARCHAR(20) DEFAULT NULL COMMENT '下单渠道：WECHAT/ALIPAY/MEITUAN/ELEME/H5',
  remark VARCHAR(500) DEFAULT NULL COMMENT '顾客备注',
  idempotency_key VARCHAR(64) DEFAULT NULL COMMENT '下单幂等键（按渠道/门店/操作员等作用域拼接，防重复提交；允许多 NULL）',
  operator_id BIGINT UNSIGNED DEFAULT NULL COMMENT '下单/收银操作员 → users.id（POS 渠道必填）',
  ordered_at DATETIME(3) DEFAULT NULL COMMENT '下单时间（业务时间）',
  paid_at DATETIME(3) DEFAULT NULL COMMENT '支付成功时间',
  completed_at DATETIME(3) DEFAULT NULL COMMENT '订单完成时间',
  cancelled_at DATETIME(3) DEFAULT NULL COMMENT '取消时间',
  kitchen_claimed_by BIGINT UNSIGNED DEFAULT NULL COMMENT '厨房认领人 → users.id；点「开始制作」写入',
  kitchen_claimed_at DATETIME(3) DEFAULT NULL COMMENT '厨房认领时间',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '记录创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '记录更新时间',
  UNIQUE KEY uk_idempotency (idempotency_key),
  INDEX idx_orders_customer (customer_id, created_at),
  INDEX idx_orders_table (table_id, created_at),
  INDEX idx_orders_dining_session (dining_session_id),
  INDEX idx_orders_status (status, created_at),
  INDEX idx_orders_payment (payment_status, created_at),
  INDEX idx_orders_status_paid (status, paid_at),
  INDEX idx_orders_store_created (store_id, created_at),
  INDEX idx_orders_store_paid (store_id, paid_at),
  INDEX idx_orders_store_pay_paid (store_id, payment_status, paid_at),
  INDEX idx_orders_kitchen_claim (kitchen_claimed_by, status),
  INDEX idx_orders_operator (operator_id),
  CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers(id),
  CONSTRAINT fk_orders_table FOREIGN KEY (table_id) REFERENCES tables(id),
  CONSTRAINT fk_orders_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_orders_operator FOREIGN KEY (operator_id) REFERENCES users(id),
  CONSTRAINT fk_orders_kitchen_claim FOREIGN KEY (kitchen_claimed_by) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单主表';

CREATE TABLE IF NOT EXISTS order_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  order_id BIGINT UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '商品 → products.id（快照时有效 ID）',
  product_name_snapshot VARCHAR(100) NOT NULL COMMENT '下单时商品名称快照',
  unit_price_snapshot DECIMAL(10,2) NOT NULL COMMENT '下单时单价快照（含基础价，不含规格加价）',
  quantity INT NOT NULL DEFAULT 1 COMMENT '购买数量',
  subtotal DECIMAL(10,2) NOT NULL COMMENT '行小计 = (单价+规格加价)×数量',
  remark VARCHAR(255) DEFAULT NULL COMMENT '单品备注（如 少葱）',
  INDEX idx_oi_order (order_id),
  CONSTRAINT fk_oi_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
  CONSTRAINT fk_oi_product FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单商品明细表';

CREATE TABLE IF NOT EXISTS order_item_options (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  order_item_id BIGINT UNSIGNED NOT NULL COMMENT '订单明细 → order_items.id',
  group_name_snapshot VARCHAR(50) NOT NULL COMMENT '规格组名称快照',
  option_name_snapshot VARCHAR(50) NOT NULL COMMENT '选项名称快照',
  option_code VARCHAR(50) NOT NULL COMMENT '选项编码快照（口味统计用）',
  extra_price_snapshot DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '下单时该选项加价快照',
  quantity INT NOT NULL DEFAULT 1 COMMENT '该选项数量（多选场景）',
  INDEX idx_oio_item (order_item_id),
  CONSTRAINT fk_oio_item FOREIGN KEY (order_item_id) REFERENCES order_items(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单明细规格快照表';

CREATE TABLE IF NOT EXISTS order_status_history (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  order_id BIGINT UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  from_status VARCHAR(20) DEFAULT NULL COMMENT '变更前状态',
  to_status VARCHAR(20) NOT NULL COMMENT '变更后状态',
  source VARCHAR(30) NOT NULL COMMENT '变更来源：STAFF=员工 | PAYMENT=支付 | SYSTEM=系统 | DELIVERY=外卖',
  operator_id BIGINT UNSIGNED DEFAULT NULL COMMENT '操作员工 → users.id（如有）',
  remark VARCHAR(255) DEFAULT NULL COMMENT '变更说明',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '变更时间',
  INDEX idx_osh_order (order_id, created_at),
  CONSTRAINT fk_osh_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单状态变更历史表';

CREATE TABLE IF NOT EXISTS payments (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  payment_no VARCHAR(40) NOT NULL UNIQUE COMMENT '支付单号',
  order_id BIGINT UNSIGNED NOT NULL COMMENT '关联订单 → orders.id',
  customer_id BIGINT UNSIGNED DEFAULT NULL COMMENT '付款顾客 → customers.id',
  operator_id BIGINT UNSIGNED DEFAULT NULL COMMENT '收银操作员 → users.id',
  provider VARCHAR(20) NOT NULL COMMENT '支付渠道：WECHAT | ALIPAY | CASH | MOCK（开发模拟）',
  channel VARCHAR(30) NOT NULL COMMENT '细分渠道，如 miniprogram、native、h5',
  amount DECIMAL(10,2) NOT NULL COMMENT '支付金额（元）',
  currency VARCHAR(10) NOT NULL DEFAULT 'CNY' COMMENT '币种',
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PAID/PARTIAL_REFUND/REFUNDED/CLOSED/FAILED',
  provider_trade_no VARCHAR(64) DEFAULT NULL COMMENT '第三方交易号',
  provider_order_no VARCHAR(64) DEFAULT NULL COMMENT '第三方商户订单号',
  callback_raw JSON DEFAULT NULL COMMENT '支付回调原文（审计）',
  paid_at DATETIME(3) DEFAULT NULL COMMENT '支付成功时间',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  UNIQUE KEY uk_provider_trade (provider, provider_trade_no),
  INDEX idx_payments_order (order_id),
  INDEX idx_payments_no (payment_no),
  INDEX idx_payments_order_status (order_id, status),
  INDEX idx_payments_created (created_at),
  INDEX idx_payments_paid_at (paid_at),
  INDEX idx_payments_status_paid (status, paid_at),
  INDEX idx_payments_operator (operator_id, paid_at),
  CONSTRAINT fk_payments_order FOREIGN KEY (order_id) REFERENCES orders(id),
  CONSTRAINT fk_payments_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='支付单表';

CREATE TABLE IF NOT EXISTS payment_refunds (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  refund_no VARCHAR(40) NOT NULL UNIQUE COMMENT '退款单号',
  payment_id BIGINT UNSIGNED NOT NULL COMMENT '原支付单 → payments.id',
  order_id BIGINT UNSIGNED NOT NULL COMMENT '关联订单 → orders.id',
  amount DECIMAL(10,2) NOT NULL COMMENT '退款金额',
  reason VARCHAR(255) DEFAULT NULL COMMENT '退款原因',
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PROCESSING/SUCCESS/FAILED/CLOSED',
  provider_refund_no VARCHAR(64) DEFAULT NULL COMMENT '第三方退款单号',
  callback_raw JSON DEFAULT NULL COMMENT '退款回调/查询原文',
  fail_reason VARCHAR(255) DEFAULT NULL COMMENT '失败或关闭原因',
  succeeded_at DATETIME(3) DEFAULT NULL COMMENT '退款成功确认时间',
  operator_id BIGINT UNSIGNED DEFAULT NULL COMMENT '发起退款员工 → users.id',
  retry_count INT NOT NULL DEFAULT 0 COMMENT '主动查询/重试次数',
  next_retry_at DATETIME(3) DEFAULT NULL COMMENT '下次允许重试时间',
  last_error VARCHAR(500) DEFAULT NULL COMMENT '最近一次失败摘要',
  idempotency_key VARCHAR(64) DEFAULT NULL COMMENT '退款幂等键（防重复发起）',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  UNIQUE KEY uk_refund_idempotency (idempotency_key),
  INDEX idx_refunds_status (status),
  INDEX idx_refunds_payment (payment_id),
  INDEX idx_refunds_operator (operator_id),
  INDEX idx_refunds_succeeded (status, succeeded_at),
  INDEX idx_refunds_retry (status, next_retry_at),
  CONSTRAINT fk_refund_payment FOREIGN KEY (payment_id) REFERENCES payments(id),
  CONSTRAINT fk_refund_order FOREIGN KEY (order_id) REFERENCES orders(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='支付退款单表';

-- 收银班次：备用金、交班与日结
CREATE TABLE IF NOT EXISTS cashier_shifts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  operator_id BIGINT UNSIGNED NOT NULL COMMENT '收银员 → users.id',
  opening_float DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '开班备用金（元）',
  status VARCHAR(20) NOT NULL DEFAULT 'OPEN' COMMENT 'OPEN=营业中 CLOSED=已交班',
  opened_at DATETIME(3) NOT NULL COMMENT '开班时间',
  closed_at DATETIME(3) DEFAULT NULL COMMENT '交班时间',
  total_cash DECIMAL(10,2) DEFAULT NULL COMMENT '本班现金收款',
  total_wechat DECIMAL(10,2) DEFAULT NULL COMMENT '本班微信收款',
  total_alipay DECIMAL(10,2) DEFAULT NULL COMMENT '本班支付宝收款',
  total_amount DECIMAL(10,2) DEFAULT NULL COMMENT '本班收款合计',
  closing_cash DECIMAL(10,2) DEFAULT NULL COMMENT '交班实盘现金（含备用金）',
  cash_difference DECIMAL(10,2) DEFAULT NULL COMMENT '现金长短款',
  closed_by BIGINT UNSIGNED DEFAULT NULL COMMENT '确认日结的管理员 → users.id',
  remark VARCHAR(500) DEFAULT NULL COMMENT '交班备注',
  open_operator_key BIGINT UNSIGNED
    GENERATED ALWAYS AS (IF(status = 'OPEN', operator_id, NULL)) STORED
    COMMENT 'OPEN 时为 operator_id，CLOSED 为 NULL；用于唯一约束',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  INDEX idx_shift_store_open (store_id, opened_at),
  INDEX idx_shift_operator_status (operator_id, status),
  UNIQUE INDEX uk_cashier_shift_one_open (open_operator_key),
  CONSTRAINT fk_shift_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_shift_operator FOREIGN KEY (operator_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='收银班次表';

-- POS 退款申请：收银员申请 → 店长/超管审批并执行
CREATE TABLE IF NOT EXISTS pos_refund_requests (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  order_id BIGINT UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  payment_id BIGINT UNSIGNED NOT NULL COMMENT '主支付单（组合支付时为分摊首笔）→ payments.id',
  amount DECIMAL(10,2) NOT NULL COMMENT '申请退款金额（元）',
  executed_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '已成功执行退款合计（组合分摊）',
  reason VARCHAR(255) NOT NULL COMMENT '申请原因',
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/REJECTED/CANCELLED/SUCCESS/FAILED',
  requested_by BIGINT UNSIGNED NOT NULL COMMENT '申请人（收银员）→ users.id',
  reviewed_by BIGINT UNSIGNED DEFAULT NULL COMMENT '审批人 → users.id',
  reviewed_at DATETIME(3) DEFAULT NULL COMMENT '审批时间',
  review_remark VARCHAR(500) DEFAULT NULL COMMENT '审批备注（拒绝原因等）',
  refund_id BIGINT UNSIGNED DEFAULT NULL COMMENT '执行后的退款单 → payment_refunds.id',
  fail_reason VARCHAR(255) DEFAULT NULL COMMENT '执行失败原因',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  INDEX idx_pos_refund_store_status (store_id, status, created_at),
  INDEX idx_pos_refund_order (order_id),
  INDEX idx_pos_refund_requester (requested_by, created_at),
  CONSTRAINT fk_pos_refund_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_pos_refund_order FOREIGN KEY (order_id) REFERENCES orders(id),
  CONSTRAINT fk_pos_refund_payment FOREIGN KEY (payment_id) REFERENCES payments(id),
  CONSTRAINT fk_pos_refund_requester FOREIGN KEY (requested_by) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='收银退款申请';

CREATE TABLE IF NOT EXISTS payment_status_history (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  payment_id BIGINT UNSIGNED NOT NULL COMMENT '支付单 → payments.id',
  from_status VARCHAR(20) DEFAULT NULL COMMENT '变更前状态',
  to_status VARCHAR(20) NOT NULL COMMENT '变更后状态',
  source VARCHAR(30) NOT NULL COMMENT 'CREATE/CALLBACK/REFUND/QUERY',
  remark VARCHAR(255) DEFAULT NULL COMMENT '备注',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '变更时间',
  INDEX idx_psh_payment (payment_id, created_at),
  CONSTRAINT fk_psh_payment FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='支付状态变更历史表';

-- 支付回调幂等回执（与 Redis 双保险，防重复入账）
CREATE TABLE IF NOT EXISTS payment_callback_receipts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  provider VARCHAR(20) NOT NULL COMMENT '支付渠道',
  receipt_key VARCHAR(128) NOT NULL COMMENT '幂等键（如 notify_id + 交易号）',
  payment_no VARCHAR(40) DEFAULT NULL COMMENT '关联支付单号',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '首次收到时间',
  UNIQUE KEY uk_receipt (provider, receipt_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='支付回调幂等回执表';

-- Webhook 收件箱（先落库再异步消费，适用于外卖/支付等）
CREATE TABLE IF NOT EXISTS webhook_inbox (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  channel VARCHAR(30) NOT NULL COMMENT 'WECHAT_PAY/ALIPAY/MEITUAN/ELEME',
  event_id VARCHAR(128) NOT NULL COMMENT '平台事件唯一 ID（幂等）',
  event_type VARCHAR(50) DEFAULT NULL COMMENT '事件类型',
  payload MEDIUMTEXT NOT NULL COMMENT '原始报文',
  headers_json TEXT DEFAULT NULL COMMENT 'HTTP 头 JSON',
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PROCESSING/DONE/FAILED',
  error_message VARCHAR(500) DEFAULT NULL COMMENT '处理失败原因',
  attempt_count INT NOT NULL DEFAULT 0 COMMENT '消费尝试次数',
  last_error VARCHAR(500) DEFAULT NULL COMMENT '最近一次错误摘要',
  next_retry_at DATETIME(3) DEFAULT NULL COMMENT '下次允许重试时间',
  locked_at DATETIME(3) DEFAULT NULL COMMENT '消费者加锁时间',
  locked_by VARCHAR(64) DEFAULT NULL COMMENT '锁持有者标识（实例/worker）',
  received_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '接收时间',
  processed_at DATETIME(3) DEFAULT NULL COMMENT '处理完成时间',
  UNIQUE KEY uk_webhook_event (channel, event_id),
  INDEX idx_wi_status (status, received_at),
  INDEX idx_wi_retry (status, next_retry_at),
  INDEX idx_wi_locked (locked_at, locked_by)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Webhook 异步收件箱';

-- 财务分录账本（收款/退款/调账审计；与支付流水互补）
CREATE TABLE IF NOT EXISTS financial_ledger (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  entry_type VARCHAR(20) NOT NULL COMMENT 'SALE=收款 | REFUND=退款 | ADJUSTMENT=调账',
  channel VARCHAR(30) DEFAULT NULL COMMENT '业务渠道：WECHAT/ALIPAY/POS/MEITUAN/ELEME 等',
  provider VARCHAR(20) DEFAULT NULL COMMENT '支付提供方：WECHAT/ALIPAY/CASH/MOCK',
  amount DECIMAL(10,2) NOT NULL COMMENT '金额（元；退款可为负或按 entry_type 区分）',
  currency VARCHAR(8) NOT NULL DEFAULT 'CNY' COMMENT '币种',
  order_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联订单 → orders.id',
  payment_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联支付单 → payments.id',
  refund_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联退款单 → payment_refunds.id',
  delivery_order_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联外卖扩展单 → delivery_orders.id',
  delivery_refund_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联外卖退款 → delivery_refunds.id',
  remark VARCHAR(500) DEFAULT NULL COMMENT '备注',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入账时间',
  INDEX idx_fl_store_created (store_id, created_at),
  INDEX idx_fl_store_ch_type_created (store_id, channel, entry_type, created_at),
  INDEX idx_fl_entry_type (entry_type, created_at),
  INDEX idx_fl_order (order_id),
  INDEX idx_fl_payment (payment_id),
  INDEX idx_fl_refund (refund_id),
  UNIQUE KEY uk_fl_payment (payment_id),
  UNIQUE KEY uk_fl_refund (refund_id),
  UNIQUE KEY uk_fl_delivery_refund (delivery_refund_id),
  CONSTRAINT fk_fl_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='财务分录账本';

-- 积分变动流水（与 customers.points 对账）
CREATE TABLE IF NOT EXISTS point_ledger (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  customer_id BIGINT UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  order_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联订单 → orders.id',
  refund_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联退款单 → payment_refunds.id',
  delta INT NOT NULL COMMENT '积分变动（可正可负）',
  balance_after INT NOT NULL COMMENT '变动后余额',
  entry_type VARCHAR(30) NOT NULL COMMENT '变动类型：EARN/REFUND_DEDUCT/ADJUST 等',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '记录时间',
  earn_order_id BIGINT UNSIGNED
    GENERATED ALWAYS AS (IF(entry_type = 'EARN', order_id, NULL)) STORED
    COMMENT 'EARN 幂等键：每单至多一条发放',
  INDEX idx_pl_customer_created (customer_id, created_at),
  INDEX idx_pl_order (order_id),
  INDEX idx_pl_refund (refund_id),
  UNIQUE KEY uk_pl_refund (refund_id),
  UNIQUE KEY uk_pl_earn_order (earn_order_id),
  CONSTRAINT fk_pl_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='会员积分流水';

-- ============================================================================
-- 六、外卖（美团 / 饿了么）
-- 用途：平台订单接入、SKU 映射、退款与回调审计
-- ============================================================================

CREATE TABLE IF NOT EXISTS delivery_product_maps (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  platform VARCHAR(20) NOT NULL COMMENT 'MEITUAN=美团 | ELEME=饿了么',
  platform_sku_id VARCHAR(64) NOT NULL COMMENT '平台 SKU ID',
  platform_spu_code VARCHAR(64) DEFAULT NULL COMMENT '平台 SPU / app_food_code',
  app_food_code VARCHAR(64) DEFAULT NULL COMMENT '本系统对外商品编码',
  option_signature VARCHAR(128) DEFAULT NULL COMMENT '规格组合签名（区分同 SKU 不同加料）',
  platform_sku_name VARCHAR(100) DEFAULT NULL COMMENT '平台侧 SKU 名称',
  product_id BIGINT UNSIGNED NOT NULL COMMENT '映射的本系统商品 → products.id',
  option_map_json JSON DEFAULT NULL COMMENT '平台规格项 → 本系统 options 的映射 JSON',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  UNIQUE KEY uk_platform_sku (store_id, platform, platform_sku_id),
  INDEX idx_dpm_product (product_id),
  CONSTRAINT fk_dpm_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_dpm_product FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='外卖平台商品映射表';

CREATE TABLE IF NOT EXISTS delivery_orders (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  order_id BIGINT UNSIGNED NOT NULL UNIQUE COMMENT '统一订单 → orders.id（一对一）',
  platform VARCHAR(20) NOT NULL COMMENT 'MEITUAN | ELEME',
  platform_order_id VARCHAR(64) NOT NULL COMMENT '平台订单号',
  platform_day_seq VARCHAR(32) DEFAULT NULL COMMENT '平台当日流水号/取餐号',
  delivery_status VARCHAR(30) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/ACCEPTED/READY/DELIVERING/DONE/CANCELLED',
  recipient_name VARCHAR(50) DEFAULT NULL COMMENT '收件人姓名',
  recipient_phone VARCHAR(20) DEFAULT NULL COMMENT '收件人电话（可能是隐私号）',
  recipient_address VARCHAR(500) DEFAULT NULL COMMENT '配送地址',
  rider_name VARCHAR(50) DEFAULT NULL COMMENT '骑手姓名',
  rider_phone VARCHAR(20) DEFAULT NULL COMMENT '骑手电话',
  expected_delivery_at DATETIME(3) DEFAULT NULL COMMENT '预计送达时间',
  platform_fee DECIMAL(10,2) DEFAULT 0.00 COMMENT '平台服务费',
  packing_fee DECIMAL(10,2) DEFAULT 0.00 COMMENT '打包费（平台侧）',
  delivery_fee DECIMAL(10,2) DEFAULT 0.00 COMMENT '配送费',
  logistics_status VARCHAR(30) DEFAULT NULL COMMENT '物流配送状态',
  logistics_code VARCHAR(50) DEFAULT NULL COMMENT '物流状态码',
  refund_status VARCHAR(30) DEFAULT 'NONE' COMMENT 'NONE/PENDING/APPROVED/REJECTED',
  urge_count INT NOT NULL DEFAULT 0 COMMENT '顾客催单次数',
  completed_at DATETIME(3) DEFAULT NULL COMMENT '送达/完成时间',
  raw_payload JSON DEFAULT NULL COMMENT '平台原始订单 JSON',
  last_sync_at DATETIME(3) DEFAULT NULL COMMENT '最近一次与平台同步时间',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  UNIQUE KEY uk_platform_order (platform, platform_order_id),
  INDEX idx_do_status (delivery_status, created_at),
  CONSTRAINT fk_do_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='外卖订单扩展表';

CREATE TABLE IF NOT EXISTS platform_store_bindings (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '本系统门店 → stores.id',
  platform VARCHAR(20) NOT NULL COMMENT 'MEITUAN | ELEME',
  platform_shop_id VARCHAR(64) NOT NULL COMMENT '平台门店 ID',
  platform_shop_name VARCHAR(100) DEFAULT NULL COMMENT '平台门店名称',
  access_token TEXT DEFAULT NULL COMMENT 'OAuth access_token',
  refresh_token TEXT DEFAULT NULL COMMENT 'OAuth refresh_token',
  token_expires_at DATETIME(3) DEFAULT NULL COMMENT 'token 过期时间',
  extra_config JSON DEFAULT NULL COMMENT '扩展配置（app_key、回调地址等）',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1=已绑定 0=已解绑',
  auto_sync_open TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否按营业时间自动开/关店（默认关闭，逐店开启）',
  platform_open_status VARCHAR(20) DEFAULT NULL COMMENT '最近同步到平台的营业状态 OPEN/REST/OFFLINE',
  last_sync_at DATETIME(3) DEFAULT NULL COMMENT '最近一次与平台同步时间',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  UNIQUE KEY uk_store_platform_shop (store_id, platform, platform_shop_id),
  CONSTRAINT fk_psb_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='外卖平台门店授权绑定表';

CREATE TABLE IF NOT EXISTS delivery_refunds (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  delivery_order_id BIGINT UNSIGNED NOT NULL COMMENT '外卖扩展单 → delivery_orders.id',
  order_id BIGINT UNSIGNED NOT NULL COMMENT '统一订单 → orders.id',
  platform VARCHAR(20) NOT NULL COMMENT 'MEITUAN | ELEME',
  platform_refund_id VARCHAR(64) DEFAULT NULL COMMENT '平台退款单号',
  refund_type VARCHAR(30) NOT NULL DEFAULT 'FULL' COMMENT 'FULL=全额 | PARTIAL=部分',
  amount DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '退款金额',
  reason VARCHAR(500) DEFAULT NULL COMMENT '退款原因',
  status VARCHAR(30) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/APPROVED/REJECTED',
  raw_payload JSON DEFAULT NULL COMMENT '平台退款报文',
  handled_at DATETIME(3) DEFAULT NULL COMMENT '处理完成时间',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  INDEX idx_dr_order (order_id),
  CONSTRAINT fk_dr_do FOREIGN KEY (delivery_order_id) REFERENCES delivery_orders(id) ON DELETE CASCADE,
  CONSTRAINT fk_dr_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='外卖退款单表';

CREATE TABLE IF NOT EXISTS delivery_callback_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  platform VARCHAR(20) NOT NULL COMMENT 'MEITUAN | ELEME',
  event_type VARCHAR(50) NOT NULL COMMENT '回调事件类型',
  event_id VARCHAR(128) DEFAULT NULL COMMENT '平台事件 ID（幂等去重）',
  platform_order_id VARCHAR(64) DEFAULT NULL COMMENT '平台订单号',
  order_id BIGINT UNSIGNED DEFAULT NULL COMMENT '本系统订单 ID（解析后填入）',
  payload JSON DEFAULT NULL COMMENT '回调原文',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '接收时间',
  UNIQUE KEY uk_dcl_event (platform, event_id),
  INDEX idx_dcl_platform_order (platform, platform_order_id, created_at),
  INDEX idx_dcl_event (event_type, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='外卖回调审计日志表';

CREATE TABLE IF NOT EXISTS store_business_hours (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  weekday TINYINT NOT NULL COMMENT '星期几：1=周一 ... 7=周日',
  slot_index INT NOT NULL DEFAULT 0 COMMENT '当日第几时段，从 0 开始',
  open_time TIME NOT NULL COMMENT '营业开始时间',
  close_time TIME NOT NULL COMMENT '营业结束时间（<=开始时间视为跨午夜）',
  cross_day TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否跨午夜',
  is_open TINYINT(1) NOT NULL DEFAULT 1 COMMENT '该时段是否营业',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  UNIQUE KEY uk_sbh_store_day_slot (store_id, weekday, slot_index),
  INDEX idx_sbh_store (store_id),
  CONSTRAINT fk_sbh_store FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='门店营业时间（多时段/跨午夜）';

CREATE TABLE IF NOT EXISTS platform_category_maps (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED DEFAULT NULL COMMENT '门店 → stores.id；NULL=总部默认映射',
  platform VARCHAR(20) NOT NULL COMMENT 'MEITUAN | ELEME',
  local_category_id BIGINT UNSIGNED NOT NULL COMMENT '本地分类 → product_categories.id',
  platform_category_code VARCHAR(64) DEFAULT NULL COMMENT '平台侧分类编码',
  platform_category_name VARCHAR(100) DEFAULT NULL COMMENT '平台侧分类名称',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  UNIQUE KEY uk_pcm_store_platform_cat (store_id, platform, local_category_id),
  INDEX idx_pcm_platform (platform, status),
  CONSTRAINT fk_pcm_store FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE,
  CONSTRAINT fk_pcm_cat FOREIGN KEY (local_category_id) REFERENCES product_categories(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='本地分类 ↔ 外卖平台分类映射';

CREATE TABLE IF NOT EXISTS delivery_sync_alerts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联门店 stores.id（可空）',
  platform VARCHAR(20) DEFAULT NULL COMMENT 'MEITUAN | ELEME',
  alert_type VARCHAR(30) NOT NULL DEFAULT 'SYNC_FAIL' COMMENT 'REJECT_ORDER=库存拒单 | SYNC_FAIL=同步失败',
  title VARCHAR(200) NOT NULL COMMENT '告警标题',
  detail TEXT DEFAULT NULL COMMENT '告警详情/原因',
  platform_order_id VARCHAR(64) DEFAULT NULL COMMENT '关联平台订单号',
  resolved TINYINT NOT NULL DEFAULT 0 COMMENT '0=未处理 1=已处理',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '产生时间',
  resolved_at DATETIME(3) DEFAULT NULL COMMENT '处理时间',
  INDEX idx_dsa_store_resolved (store_id, resolved, created_at),
  INDEX idx_dsa_platform_order (platform_order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='外卖同步/拒单告警';

-- ============================================================================
-- 七、摄像头 / 客流 / 人脸迎宾
-- 用途：到店识别、匿名客流统计、会员迎宾语
-- ============================================================================

CREATE TABLE IF NOT EXISTS cameras (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '所属门店 → stores.id',
  name VARCHAR(100) NOT NULL COMMENT '摄像头名称/位置',
  camera_type VARCHAR(20) NOT NULL COMMENT 'USB=本机摄像头 | RTSP=网络流 | HTTP=HTTP 流',
  source_url VARCHAR(500) NOT NULL COMMENT '设备号或 RTSP/HTTP 地址',
  username VARCHAR(100) DEFAULT NULL COMMENT '拉流用户名',
  password_encrypted VARCHAR(500) DEFAULT NULL COMMENT '加密后的拉流密码',
  status VARCHAR(20) NOT NULL DEFAULT 'OFFLINE' COMMENT 'ONLINE/OFFLINE/RECONNECTING',
  fps INT NOT NULL DEFAULT 25 COMMENT '视频采集帧率',
  detect_fps INT NOT NULL DEFAULT 3 COMMENT '人脸检测采样帧率（降负载）',
  last_online_at DATETIME(3) DEFAULT NULL COMMENT '最近在线时间',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  CONSTRAINT fk_cameras_store FOREIGN KEY (store_id) REFERENCES stores(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='门店摄像头表';

CREATE TABLE IF NOT EXISTS visitor_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  visitor_no VARCHAR(64) NOT NULL COMMENT '匿名访客编号（非会员）',
  camera_id BIGINT UNSIGNED NOT NULL COMMENT '摄像头 → cameras.id',
  event_type VARCHAR(30) NOT NULL COMMENT 'ENTER=进入 | EXIT=离开 | PASS=路过',
  event_time DATETIME(3) NOT NULL COMMENT '事件发生时间',
  snapshot_path VARCHAR(500) DEFAULT NULL COMMENT '抓拍图存储路径',
  expires_at DATETIME(3) NOT NULL COMMENT '数据保留到期时间（到期可清理）',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入库时间',
  INDEX idx_ve_camera_time (camera_id, event_time),
  INDEX idx_ve_expires (expires_at),
  CONSTRAINT fk_ve_camera FOREIGN KEY (camera_id) REFERENCES cameras(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='匿名客流事件表';

CREATE TABLE IF NOT EXISTS face_profiles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  customer_id BIGINT UNSIGNED NOT NULL COMMENT '顾客 → customers.id',
  feature_data BLOB NOT NULL COMMENT '加密后的人脸特征向量',
  feature_version VARCHAR(20) NOT NULL DEFAULT 'v1' COMMENT '特征提取算法版本',
  consent_status VARCHAR(20) NOT NULL DEFAULT 'AUTHORIZED' COMMENT 'AUTHORIZED=已授权 | REVOKED=已撤销删除',
  consent_at DATETIME(3) NOT NULL COMMENT '授权采集时间',
  revoked_at DATETIME(3) DEFAULT NULL COMMENT '撤销授权时间',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  INDEX idx_face_customer (customer_id, consent_status),
  CONSTRAINT fk_face_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='会员人脸特征档案表';

CREATE TABLE IF NOT EXISTS welcome_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  customer_id BIGINT UNSIGNED NOT NULL COMMENT '被识别顾客 → customers.id',
  camera_id BIGINT UNSIGNED DEFAULT NULL COMMENT '触发摄像头 → cameras.id',
  welcome_text VARCHAR(500) NOT NULL COMMENT '播报/展示的欢迎语（可含口味推荐）',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '迎宾时间',
  INDEX idx_welcome_customer (customer_id, created_at),
  CONSTRAINT fk_welcome_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='人脸迎宾记录表';

-- ============================================================================
-- 八、全局系统配置
-- 用途：全店共用的在线可改参数（非门店级）
-- ============================================================================

CREATE TABLE IF NOT EXISTS system_settings (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  setting_key VARCHAR(100) NOT NULL UNIQUE COMMENT '配置键',
  setting_value TEXT NOT NULL COMMENT '配置值',
  description VARCHAR(255) DEFAULT NULL COMMENT '配置说明',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '最后修改时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统全局配置表';

-- 厨房小票打印任务（落库 + 失败退避重试）
CREATE TABLE IF NOT EXISTS print_jobs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
  order_id BIGINT UNSIGNED NOT NULL COMMENT '订单 → orders.id',
  ticket_type VARCHAR(20) NOT NULL DEFAULT 'KITCHEN' COMMENT 'KITCHEN/URGE/CANCEL',
  content_hash VARCHAR(64) DEFAULT NULL COMMENT '内容 SHA-256',
  content TEXT DEFAULT NULL COMMENT '小票内容（已清控制字符，可截断）',
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PRINTING/SUCCESS/FAILED/RETRYING',
  attempt_count INT NOT NULL DEFAULT 0 COMMENT '已尝试次数（含当前）',
  next_retry_at DATETIME(3) DEFAULT NULL COMMENT '下次允许重试时间（指数退避）',
  last_error VARCHAR(500) DEFAULT NULL COMMENT '最近一次失败摘要',
  locked_at DATETIME(3) DEFAULT NULL COMMENT '消费者加锁时间',
  locked_by VARCHAR(64) DEFAULT NULL COMMENT '锁持有者标识（实例/worker）',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  succeeded_at DATETIME(3) DEFAULT NULL COMMENT '打印成功时间',
  INDEX idx_pj_order (order_id, ticket_type),
  INDEX idx_pj_store_status (store_id, status, created_at),
  INDEX idx_pj_retry (status, next_retry_at),
  INDEX idx_pj_locked (locked_at, locked_by),
  CONSTRAINT fk_pj_store FOREIGN KEY (store_id) REFERENCES stores(id),
  CONSTRAINT fk_pj_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='厨房小票打印任务表';

CREATE TABLE IF NOT EXISTS outbox_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  aggregate_type VARCHAR(32) NOT NULL COMMENT '聚合类型：refund / paid_side_effect / ...',
  aggregate_id BIGINT UNSIGNED NOT NULL COMMENT '聚合 ID（refund_id / order_id / ...）',
  event_kind VARCHAR(64) NOT NULL COMMENT '事件类型：refund_finalize / points_award / coupon_mark_used / pref_update / points_deduct_refund / pref_recompute',
  payload MEDIUMTEXT DEFAULT NULL COMMENT 'JSON 负载（参数快照）',
  status VARCHAR(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING / PROCESSING / DONE / FAILED',
  attempt_count INT NOT NULL DEFAULT 0 COMMENT '消费尝试次数',
  max_attempts INT NOT NULL DEFAULT 8 COMMENT '最大重试次数',
  next_retry_at DATETIME(3) DEFAULT NULL COMMENT '下次允许重试时间',
  locked_at DATETIME(3) DEFAULT NULL COMMENT '消费者加锁时间',
  locked_by VARCHAR(64) DEFAULT NULL COMMENT '锁持有者标识（hostname:pid）',
  last_error VARCHAR(500) DEFAULT NULL COMMENT '最近一次错误摘要',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  processed_at DATETIME(3) DEFAULT NULL COMMENT '处理完成时间',
  UNIQUE KEY uq_outbox_agg (aggregate_type, aggregate_id, event_kind),
  INDEX idx_oe_status_retry (status, next_retry_at),
  INDEX idx_oe_status_locked (status, locked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='DB 事务型 outbox 事件';

SET FOREIGN_KEY_CHECKS = 1;

-- 完成：本文件已包含生产基础数据；随后运行 init_admin.py。
-- END CANONICAL SCHEMA

-- BEGIN PRODUCTION SEED
-- ============================================================================
-- 智能面馆 — 生产环境种子数据
-- ============================================================================
-- 前置：本文件上方 CANONICAL SCHEMA 分段
-- 后续：python scripts/init_admin.py（如需重置超管密码；本文件已含 admin/hsf/syxm/cwm）
-- 多门店：第六节按实际增删 store_no
--
-- ⚠ 会先清空全部业务表数据，再写入种子（不可对线上有业务数据的库随意执行）
--
-- 退款权限策略：
--   1) 收银员 CASHIER：无 payment:refund；收银渠道须「本班订单申请 → 店长审批」
--   2) 店长 STORE_ADMIN / 超管 SUPER_ADMIN：持有 payment:refund，可管理端直连或走 POS 审批页
--   3) 后厨/运营：不得持有 payment:refund（见文末 DELETE 校正）
-- ============================================================================

SET NAMES utf8mb4;
USE smart_noodle_house;

-- ---------------------------------------------------------------------------
-- 〇、清空全部业务表（与本文件结构段对齐；TRUNCATE 同时重置自增）
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
INSERT INTO `smart_noodle_house`.`stores` (`id`, `store_no`, `name`, `address`, `phone`, `latitude`, `longitude`, `status`, `created_at`, `updated_at`) VALUES 
(1, 'SH01', '肠旺面(总店)', '城南红砖路88号', '151-9691-4600', 30.8719, 105.3884, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(2, 'SH02', '肠旺面(二店)', '城南红砖路188号', '151-9691-4600', 30.8719, 105.3884, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

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

-- true/false
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'xpyun_enabled', 'false', '厨房打印总开关'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'xpyun_enabled');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'printer_mode', 'local', '打印机模式'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'printer_mode');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'kitchen_ticket_order_no', 'barcode', '厨房票订单号样式 barcode/text/off'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'kitchen_ticket_order_no');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'xpyun_print_copies', '1', '小票打印份数'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'xpyun_print_copies');
INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'xpyun_voice', '0', '云打印语音 0~3'
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE setting_key = 'xpyun_voice');

INSERT INTO system_settings (setting_key, setting_value, description)
SELECT 'printer_host', '192.168.1.150', '打印机 IP'
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

INSERT INTO product_categories (store_id, category_no, name, description, sort_order, status, created_at, updated_at) VALUES
(1, 'SH01-ZhaoPaiMian', '招牌面·汤面', '射洪招牌汤面：鸡杂/杂酱/豌杂/笋子牛肉/蹄花/肠旺面', 6, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, 'SH01-GanBanMian', '干拌面', '干拌宽面、烧椒面（可切汤/干拌）', 7, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, 'SH01-ChaoShouShuiJiao', '抄手·水饺', '清汤/红油抄手与红油水饺', 8, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, 'SH01-TangYuan', '甜香汤圆', '汤圆荷包蛋、红糖小汤圆（甜品，咸甜分开）', 9, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, 'SH01-BaoZiDianXin', '包子·点心', '香葱包、酱肉包，支持单个起售', 10, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- ============================================================================
-- 二、新增规格组（1-13：量/加料/辣/麻/葱/香菜/蒜末/姜末/面型/口感/汤汁/加臊子/
--     加蛋加菜/汤底/皮薄厚/甜度/荷包蛋熟度/汤圆数量/包子个数/是否加热）
--     store_id=NULL 表示总部共享，全店可绑（与原有 0000- 编码一致）
-- ============================================================================
INSERT INTO option_groups (id, store_id, name, code, selection_type, is_required, sort_order, status, created_at, updated_at) VALUES
(1,  1, '姜末', 'SH01-JiangMo', 'SINGLE', 0, 7, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(2,  1, '面型', 'SH01-MianXing', 'SINGLE', 0, 8, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(3,  1, '口感', 'SH01-KouGan', 'SINGLE', 0, 9, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(4,  1, '汤汁', 'SH01-TangZhi', 'SINGLE', 0, 10, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(5,  1, '加臊子', 'SH01-JiaSaoZi', 'MULTIPLE', 0, 11, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(6,  1, '加蛋加菜', 'SH01-JiaDanJiaCai', 'MULTIPLE', 0, 12, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(7,  1, '汤底', 'SH01-TangDi', 'SINGLE', 0, 13, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(8,  1, '皮薄厚', 'SH01-PiBaoHou', 'SINGLE', 0, 14, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(9,  1, '甜度', 'SH01-TianDu', 'SINGLE', 0, 15, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(10, 1, '荷包蛋熟度', 'SH01-ShuDu', 'SINGLE', 0, 16, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(11, 1, '汤圆数量', 'SH01-TangYuanShuLiang', 'SINGLE', 1, 17, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(12, 1, '包子个数', 'SH01-BaoZiGeShu', 'SINGLE', 1, 18, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(13, 1, '是否加热', 'SH01-ShiFouJiaRe', 'SINGLE', 1, 19, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- ============================================================================
-- 三、新增规格选项
-- ============================================================================

-- 3.1 姜末（group 1）★ 用户要求的葱/姜/蒜之“姜”
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(1, '不加姜末', 'SH01-JiangMo-BuJiaJiangMo', 0.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, '微量姜末', 'SH01-JiangMo-WeiLiangJiangMo', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, '正常姜末', 'SH01-JiangMo-ZhengChangJiangMo', 0.00, 2, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, '加量姜末', 'SH01-JiangMo-JiaLiangJiangMo', 0.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.2 面型（group 2）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(2, '细面', 'SH01-MianXing-XiMian', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(2, '宽面', 'SH01-MianXing-KuanMian', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(2, '韭菜叶', 'SH01-MianXing-JiuCaiYe', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(2, '碱水面', 'SH01-MianXing-JianShuiMian', 0.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.3 口感（group 3）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(3, '软一点', 'SH01-KouGan-RuanYiDian', 0.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(3, '标准', 'SH01-KouGan-BiaoZhun', 0.00, 1, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(3, '硬一点(提黄)', 'SH01-KouGan-YingYiDian', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.4 汤汁（group 4）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(4, '正常汤', 'SH01-TangZhi-ZhengChangTang', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(4, '多汤', 'SH01-TangZhi-DuoTang', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(4, '少汤', 'SH01-TangZhi-ShaoTang', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(4, '干拌(免汤)', 'SH01-TangZhi-GanBan', 0.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.5 加臊子（group 5，多选加价；牛肉/肥肠沿用原 group 2 加料）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(5, '加杂酱', 'SH01-JiaSaoZi-ZaJiang', 3.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(5, '加鸡杂', 'SH01-JiaSaoZi-JiZa', 4.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(5, '加蹄花', 'SH01-JiaSaoZi-TiHua', 6.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(5, '加血旺', 'SH01-JiaSaoZi-XueWang', 3.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.6 加蛋加菜（group 6）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(6, '加煎蛋', 'SH01-JiaDanJiaCai-JianDan', 2.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(6, '加荷包蛋', 'SH01-JiaDanJiaCai-HeBaoDan', 2.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(6, '加豌豆', 'SH01-JiaDanJiaCai-WanDou', 1.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(6, '加青菜', 'SH01-JiaDanJiaCai-QingCai', 1.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(6, '加豆芽', 'SH01-JiaDanJiaCai-DouYa', 1.00, 4, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.7 汤底（group 7，抄手/水饺）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(7, '清汤', 'SH01-TangDi-QingTang', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(7, '红油', 'SH01-TangDi-HongYou', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(7, '酸辣', 'SH01-TangDi-SuanLa', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(7, '干拌', 'SH01-TangDi-GanBan', 0.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.8 皮薄厚（group 8）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(8, '标准', 'SH01-PiBaoHou-BiaoZhun', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(8, '皮薄', 'SH01-PiBaoHou-PiBao', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.9 甜度（group 9，汤圆）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(9, '正常糖', 'SH01-TianDu-ZhengChangTang', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(9, '少糖', 'SH01-TianDu-ShaoTang', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(9, '无糖', 'SH01-TianDu-WuTang', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.10 荷包蛋熟度（group 10）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(10, '溏心', 'SH01-ShuDu-TangXin', 0.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(10, '全熟', 'SH01-ShuDu-QuanShu', 0.00, 1, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.11 汤圆数量（group 11，必选，默认 6 个）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(11, '4个', 'SH01-TangYuanShuLiang-4Ge', 0.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(11, '6个', 'SH01-TangYuanShuLiang-6Ge', 0.00, 1, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(11, '8个', 'SH01-TangYuanShuLiang-8Ge', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.12 包子个数（group 12；基础价=1个价，其余为相对加价）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(12, '1个', 'SH01-BaoZiGeShu-1Ge', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(12, '2个', 'SH01-BaoZiGeShu-2Ge', 2.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(12, '半笼(3个)', 'SH01-BaoZiGeShu-BanLong', 4.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(12, '一笼(6个)', 'SH01-BaoZiGeShu-YiLong', 10.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.13 是否加热（group 13）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(13, '现蒸', 'SH01-ShiFouJiaRe-XianZheng', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(13, '复蒸加热', 'SH01-ShiFouJiaRe-FuZheng', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- ============================================================================
-- 四、新增 14 道餐品
--     product_no 规则：{store_no}-分类逻辑-商品拼音
--     main_image：1200×1200 方图，实际文件由配图步骤生成到 /uploads/products/
-- ============================================================================

-- 4.1 招牌面·汤面 -----------------------------------------------------------
INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-ZhaoPaiMian-JiZaMian', '鸡杂面', '爆炒鸡杂（鸡胗/鸡心）浇头，红汤细面，麻辣鲜香', '/uploads/products/jizamian.png',
  9.00, NULL, 9999, 1.00, 1, 'ON_SALE', 0, 0, 0, 0, 0, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-ZhaoPaiMian'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-ZhaoPaiMian-JiZaMian');

INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-ZhaoPaiMian-ZaJiangMian', '杂酱面', '肉末杂酱臊子，酱色油亮，经典川味干香', '/uploads/products/zajiangmian.png',
  8.00, NULL, 9999, 1.00, 1, 'ON_SALE', 0, 1, 0, 0, 1, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-ZhaoPaiMian'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-ZhaoPaiMian-ZaJiangMian');

INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-ZhaoPaiMian-WanZaMian', '豌杂面', '豌豆+杂酱双浇头，软糯耙豌豆配肉末（即“碗杂面”）', '/uploads/products/wanzamian.png',
  9.00, NULL, 9999, 1.00, 1, 'ON_SALE', 0, 0, 0, 0, 2, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-ZhaoPaiMian'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-ZhaoPaiMian-WanZaMian');

INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-ZhaoPaiMian-SunZiNiuRouMian', '笋子牛肉面', '红烧牛肉块配鲜笋，红汤醇厚，大块牛肉满足', '/uploads/products/sunziniuroumian.png',
  12.00, NULL, 9999, 1.00, 1, 'ON_SALE', 0, 1, 0, 0, 3, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-ZhaoPaiMian'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-ZhaoPaiMian-SunZiNiuRouMian');

INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-ZhaoPaiMian-TiHuaMian', '蹄花面', '炖耙糯猪蹄花，雪豆汤底，胶质浓稠', '/uploads/products/tihuamian.png',
  14.00, NULL, 9999, 1.00, 1, 'ON_SALE', 0, 0, 0, 0, 4, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-ZhaoPaiMian'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-ZhaoPaiMian-TiHuaMian');

INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-ZhaoPaiMian-ChangWangMian', '肠旺面', '★招牌：卤肥肠+血旺（鸭血）双浇，红油汤底，射洪必点', '/uploads/products/changwangmian.png',
  12.00, NULL, 9999, 1.00, 1, 'ON_SALE', 1, 1, 0, 0, 5, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-ZhaoPaiMian'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-ZhaoPaiMian-ChangWangMian');

-- 4.2 干拌面 ---------------------------------------------------------------
INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-GanBanMian-GanBanKuanMian', '干拌宽面', '宽面干拌，配杂酱/芽菜/花生碎，无汤更香', '/uploads/products/ganbankuanmian.png',
  8.00, NULL, 9999, 1.00, 1, 'ON_SALE', 0, 0, 0, 0, 0, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-GanBanMian'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-GanBanMian-GanBanKuanMian');

INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-GanBanMian-ShaoJiaoMian', '烧椒面', '炭烧青椒剁碎拌面，糊辣清香（可选汤/干拌）', '/uploads/products/shaojiaomian.png',
  9.00, NULL, 9999, 1.00, 1, 'ON_SALE', 0, 0, 0, 0, 1, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-GanBanMian'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-GanBanMian-ShaoJiaoMian');

-- 4.3 抄手·水饺 -------------------------------------------------------------
INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-ChaoShouShuiJiao-ChaoShou', '抄手', '薄皮猪肉抄手，可选清汤/红油/酸辣/干拌，皮薄馅嫩', '/uploads/products/chaoshou.png',
  8.00, NULL, 9999, 1.00, 1, 'ON_SALE', 0, 1, 0, 0, 0, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-ChaoShouShuiJiao'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-ChaoShouShuiJiao-ChaoShou');

INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-ChaoShouShuiJiao-HongYouShuiJiao', '红油水饺', '皮薄馅大水饺，淋红亮辣椒油与复制酱油，撒芝麻葱花', '/uploads/products/hongyoushuijiao.png',
  9.00, NULL, 9999, 1.00, 1, 'ON_SALE', 0, 0, 0, 0, 1, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-ChaoShouShuiJiao'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-ChaoShouShuiJiao-HongYouShuiJiao');

-- 4.4 甜香汤圆 -------------------------------------------------------------
INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-TangYuan-TangYuanHeBaoDan', '汤圆荷包蛋', '红糖水小汤圆配煎荷包蛋，甜香暖胃（早餐/夜宵）', '/uploads/products/tangyuanhebaodan.png',
  7.00, NULL, 9999, 1.00, 1, 'ON_SALE', 0, 0, 0, 0, 0, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-TangYuan'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-TangYuan-TangYuanHeBaoDan');

INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-TangYuan-HongTangXiaoTangYuan', '红糖小汤圆', '糯米小汤圆卧浓稠红糖汁，撒熟白芝麻，甜而不腻', '/uploads/products/hongtangxiaotangyuan.png',
  6.00, NULL, 9999, 1.00, 1, 'ON_SALE', 0, 0, 0, 0, 1, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-TangYuan'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-TangYuan-HongTangXiaoTangYuan');

-- 4.5 包子·点心 -------------------------------------------------------------
INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-BaoZiDianXin-XiangCongBao', '香葱包', '白白胖胖，面皮里裹新鲜香葱碎，咸香松软（1个起售）', '/uploads/products/xiangcongbao.png',
  2.00, NULL, 9999, 0.00, 1, 'ON_SALE', 0, 0, 0, 0, 0, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-BaoZiDianXin'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-BaoZiDianXin-XiangCongBao');

INSERT INTO products (store_id, category_id, product_no, name, description, main_image, price, original_price, stock, box_price, min_order_count, status, is_recommended, is_hot, no_member_discount, no_cooking, sort_order, sales_count)
SELECT s.id, c.id, 'SH01-BaoZiDianXin-JiangRouBao', '酱肉包', '酱色猪肉馅，咸甜适口，汁香浓郁（1个起售）', '/uploads/products/jiangroubao.png',
  2.50, NULL, 9999, 0.00, 1, 'ON_SALE', 0, 0, 0, 0, 1, 0
FROM stores s JOIN product_categories c ON c.store_id = s.id AND c.category_no = 'SH01-BaoZiDianXin'
WHERE s.store_no = 'SH01' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.store_id = s.id AND p.product_no = 'SH01-BaoZiDianXin-JiangRouBao');

-- ============================================================================
-- 五、商品-规格组绑定（按分类批量绑定，已存在则跳过）
-- ============================================================================

-- 5.1 招牌面·汤面 + 干拌面：完整面类规格（量/加料/辣/麻/葱/香菜/蒜末/姜末/面型/口感/汤汁/加臊子/加蛋加菜）
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, g.sort_order
FROM products p
JOIN product_categories c ON c.id = p.category_id
JOIN option_groups g ON g.code IN ('SH01-Liang','SH01-JiaLiao','SH01-La','SH01-Ma','SH01-Cong','SH01-XiangCai','SH01-SuanMo','SH01-JiangMo','SH01-MianXing','SH01-KouGan','SH01-TangZhi','SH01-JiaSaoZi','SH01-JiaDanJiaCai')
WHERE p.store_id = (SELECT id FROM stores WHERE store_no = 'SH01')
  AND c.category_no IN ('SH01-ZhaoPaiMian','SH01-GanBanMian')
  AND NOT EXISTS (SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id);

-- 5.2 抄手·水饺：量(份)/加料/辣/麻/葱/香菜/蒜末/姜末/加蛋加菜/汤底/皮薄厚
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, g.sort_order
FROM products p
JOIN product_categories c ON c.id = p.category_id
JOIN option_groups g ON g.code IN ('SH01-Liang','SH01-JiaLiao','SH01-La','SH01-Ma','SH01-Cong','SH01-XiangCai','SH01-SuanMo','SH01-JiangMo','SH01-JiaDanJiaCai','SH01-TangDi','SH01-PiBaoHou')
WHERE p.store_id = (SELECT id FROM stores WHERE store_no = 'SH01')
  AND c.category_no = 'SH01-ChaoShouShuiJiao'
  AND NOT EXISTS (SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id);

-- 5.3 汤圆荷包蛋：甜度 + 荷包蛋熟度 + 汤圆数量
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, g.sort_order
FROM products p
JOIN option_groups g ON g.code IN ('SH01-TianDu','SH01-ShuDu','SH01-TangYuanShuLiang')
WHERE p.store_id = (SELECT id FROM stores WHERE store_no = 'SH01')
  AND p.product_no = 'SH01-TangYuan-TangYuanHeBaoDan'
  AND NOT EXISTS (SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id);

-- 5.4 红糖小汤圆：甜度 + 汤圆数量
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, g.sort_order
FROM products p
JOIN option_groups g ON g.code IN ('SH01-TianDu','SH01-TangYuanShuLiang')
WHERE p.store_id = (SELECT id FROM stores WHERE store_no = 'SH01')
  AND p.product_no = 'SH01-TangYuan-HongTangXiaoTangYuan'
  AND NOT EXISTS (SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id);

-- 5.5 包子·点心：包子个数 + 是否加热
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, g.sort_order
FROM products p
JOIN product_categories c ON c.id = p.category_id
JOIN option_groups g ON g.code IN ('SH01-BaoZiGeShu','SH01-ShiFouJiaRe')
WHERE p.store_id = (SELECT id FROM stores WHERE store_no = 'SH01')
  AND c.category_no = 'SH01-BaoZiDianXin'
  AND NOT EXISTS (SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id);


-- 添加桌号（WM=外带虚拟通道，非实体桌；堂食用 A01/A02）
INSERT INTO `smart_noodle_house`.`tables` (`id`, `store_id`, `table_no`, `table_name`, `qr_token`, `wechat_qr_url`, `alipay_qr_url`, `status`, `created_at`, `updated_at`) VALUES 
(1, 1, 'WM', '外带自取', 'M7v-8H_ZLOdUXsR9UYPH7Umj-ZU0Z4JuEijy5X_w3kg', 'weixin://dl/business/?appid=wxd0964c5d02b1549d&path=pages/index/index&query=token%3DM7v-8H_ZLOdUXsR9UYPH7Umj-ZU0Z4JuEijy5X_w3kg%26channel%3Dwechat&env_version=develop', 'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages%2Findex%2Findex&query=token%3DM7v-8H_ZLOdUXsR9UYPH7Umj-ZU0Z4JuEijy5X_w3kg%26channel%3Dalipay', 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(2, 1, 'A01', '1号桌', '6jO4K28UbqPozFWvtT0uqPpR5WWwD3030ir2ORwQYWs', 'weixin://dl/business/?appid=wxd0964c5d02b1549d&path=pages/index/index&query=token%3D6jO4K28UbqPozFWvtT0uqPpR5WWwD3030ir2ORwQYWs%26channel%3Dwechat&env_version=develop', 'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages%2Findex%2Findex&query=token%3D6jO4K28UbqPozFWvtT0uqPpR5WWwD3030ir2ORwQYWs%26channel%3Dalipay', 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(3, 1, 'A02', '2号桌', 'wJYZpvc1g-FqvZ7KE-XtFaS_M115B6KCcm93Cav6VbA', 'weixin://dl/business/?appid=wxd0964c5d02b1549d&path=pages/index/index&query=token%3DwJYZpvc1g-FqvZ7KE-XtFaS_M115B6KCcm93Cav6VbA%26channel%3Dwechat&env_version=develop', 'alipays://platformapi/startapp?appId=YOUR_ALIPAY_APPID&page=pages%2Findex%2Findex&query=token%3DwJYZpvc1g-FqvZ7KE-XtFaS_M115B6KCcm93Cav6VbA%26channel%3Dalipay', 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');
-- END PRODUCTION SEED

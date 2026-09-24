-- =============================================================================
-- 本地测试数据（仅本地联调，勿导入生产）
-- 前置：已执行 sql/struct.sql + sql/data.sql（含 SH01/SH02 门店壳）
-- 本文件补齐 SH02 经营实体：分类、精简菜品、桌台、员工、测试摄像头
-- 用法（本地）：mysql ... < sql/struct.sql && mysql ... < sql/data.sql && mysql ... < sql/data_test.sql
-- =============================================================================
USE `smart_noodle_house`;
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- -----------------------------------------------------------------------------
-- 二店员工（密码哈希与 data.sql 同角色账号一致，便于本地联调）
--   cwm2 / 与店长 cwm 同密；syy2 / 与收银 syy 同密；hsf2 / 与厨房 hsf 同密
-- -----------------------------------------------------------------------------
INSERT INTO `users` (`username`, `password_hash`, `display_name`, `role_id`, `store_id`, `token_version`, `status`, `created_at`, `updated_at`)
SELECT 'cwm2', u.`password_hash`, '二店店长', r.`id`, s.`id`, 0, 1, NOW(3), NOW(3)
FROM `stores` s
JOIN `roles` r ON r.`code` = 'STORE_ADMIN'
JOIN `users` u ON u.`username` = 'cwm'
WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `users` x WHERE x.`username` = 'cwm2');

INSERT INTO `users` (`username`, `password_hash`, `display_name`, `role_id`, `store_id`, `token_version`, `status`, `created_at`, `updated_at`)
SELECT 'syy2', u.`password_hash`, '二店收银', r.`id`, s.`id`, 0, 1, NOW(3), NOW(3)
FROM `stores` s
JOIN `roles` r ON r.`code` = 'CASHIER'
JOIN `users` u ON u.`username` = 'syy'
WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `users` x WHERE x.`username` = 'syy2');

INSERT INTO `users` (`username`, `password_hash`, `display_name`, `role_id`, `store_id`, `token_version`, `status`, `created_at`, `updated_at`)
SELECT 'hsf2', u.`password_hash`, '二店厨房', r.`id`, s.`id`, 0, 1, NOW(3), NOW(3)
FROM `stores` s
JOIN `roles` r ON r.`code` = 'KITCHEN'
JOIN `users` u ON u.`username` = 'hsf'
WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `users` x WHERE x.`username` = 'hsf2');

-- -----------------------------------------------------------------------------
-- 二店分类
-- -----------------------------------------------------------------------------
INSERT INTO `product_categories` (`store_id`, `category_no`, `name`, `description`, `sort_order`, `status`, `created_at`, `updated_at`)
SELECT s.`id`, 'SH02-ZhaoPaiMian', '招牌面·汤面', '二店招牌汤面（测试）', 6, 1, NOW(3), NOW(3)
FROM `stores` s WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `product_categories` c WHERE c.`store_id` = s.`id` AND c.`category_no` = 'SH02-ZhaoPaiMian');

INSERT INTO `product_categories` (`store_id`, `category_no`, `name`, `description`, `sort_order`, `status`, `created_at`, `updated_at`)
SELECT s.`id`, 'SH02-GanBanMian', '干拌面', '二店干拌面（测试）', 7, 1, NOW(3), NOW(3)
FROM `stores` s WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `product_categories` c WHERE c.`store_id` = s.`id` AND c.`category_no` = 'SH02-GanBanMian');

INSERT INTO `product_categories` (`store_id`, `category_no`, `name`, `description`, `sort_order`, `status`, `created_at`, `updated_at`)
SELECT s.`id`, 'SH02-YinPin', '饮品', '二店饮品（测试）', 20, 1, NOW(3), NOW(3)
FROM `stores` s WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `product_categories` c WHERE c.`store_id` = s.`id` AND c.`category_no` = 'SH02-YinPin');

-- -----------------------------------------------------------------------------
-- 二店精简规格（量/辣）
-- -----------------------------------------------------------------------------
INSERT INTO `option_groups` (`store_id`, `name`, `code`, `selection_type`, `is_required`, `sort_order`, `status`, `created_at`, `updated_at`)
SELECT s.`id`, '量', 'SH02-Liang', 'SINGLE', 1, 0, 1, NOW(3), NOW(3)
FROM `stores` s WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `option_groups` g WHERE g.`store_id` = s.`id` AND g.`code` = 'SH02-Liang');

INSERT INTO `option_groups` (`store_id`, `name`, `code`, `selection_type`, `is_required`, `sort_order`, `status`, `created_at`, `updated_at`)
SELECT s.`id`, '辣', 'SH02-La', 'SINGLE', 0, 1, 1, NOW(3), NOW(3)
FROM `stores` s WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `option_groups` g WHERE g.`store_id` = s.`id` AND g.`code` = 'SH02-La');

INSERT INTO `options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`)
SELECT g.`id`, '标准', 'SH02-Liang-BiaoZhun', 0.00, 0, 1, 1, NOW(3), NOW(3)
FROM `option_groups` g WHERE g.`code` = 'SH02-Liang'
  AND NOT EXISTS (SELECT 1 FROM `options` o WHERE o.`group_id` = g.`id` AND o.`code` = 'SH02-Liang-BiaoZhun');

INSERT INTO `options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`)
SELECT g.`id`, '大份', 'SH02-Liang-DaFen', 2.00, 1, 0, 1, NOW(3), NOW(3)
FROM `option_groups` g WHERE g.`code` = 'SH02-Liang'
  AND NOT EXISTS (SELECT 1 FROM `options` o WHERE o.`group_id` = g.`id` AND o.`code` = 'SH02-Liang-DaFen');

INSERT INTO `options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`)
SELECT g.`id`, '微辣', 'SH02-La-WeiLa', 0.00, 0, 1, 1, NOW(3), NOW(3)
FROM `option_groups` g WHERE g.`code` = 'SH02-La'
  AND NOT EXISTS (SELECT 1 FROM `options` o WHERE o.`group_id` = g.`id` AND o.`code` = 'SH02-La-WeiLa');

INSERT INTO `options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`)
SELECT g.`id`, '中辣', 'SH02-La-ZhongLa', 0.00, 1, 0, 1, NOW(3), NOW(3)
FROM `option_groups` g WHERE g.`code` = 'SH02-La'
  AND NOT EXISTS (SELECT 1 FROM `options` o WHERE o.`group_id` = g.`id` AND o.`code` = 'SH02-La-ZhongLa');

INSERT INTO `options` (`group_id`, `name`, `code`, `extra_price`, `sort_order`, `is_default`, `status`, `created_at`, `updated_at`)
SELECT g.`id`, '特辣', 'SH02-La-TeLa', 0.00, 2, 0, 1, NOW(3), NOW(3)
FROM `option_groups` g WHERE g.`code` = 'SH02-La'
  AND NOT EXISTS (SELECT 1 FROM `options` o WHERE o.`group_id` = g.`id` AND o.`code` = 'SH02-La-TeLa');

-- -----------------------------------------------------------------------------
-- 二店菜品（精简可点餐联调）
-- -----------------------------------------------------------------------------
INSERT INTO `products` (
  `store_id`, `category_id`, `product_no`, `name`, `description`, `main_image`,
  `price`, `original_price`, `stock`, `box_price`, `min_order_count`, `status`,
  `is_recommended`, `is_hot`, `no_member_discount`, `no_cooking`, `sort_order`, `sales_count`
)
SELECT s.`id`, c.`id`, 'SH02-ZhaoPaiMian-ChangWangMian', '肠旺面', '二店测试招牌肠旺面',
  '/uploads/products/changwangmian.webp', 12.00, NULL, 9999, 1.00, 1, 'ON_SALE',
  1, 1, 0, 0, 0, 0
FROM `stores` s
JOIN `product_categories` c ON c.`store_id` = s.`id` AND c.`category_no` = 'SH02-ZhaoPaiMian'
WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `products` p WHERE p.`store_id` = s.`id` AND p.`product_no` = 'SH02-ZhaoPaiMian-ChangWangMian');

INSERT INTO `products` (
  `store_id`, `category_id`, `product_no`, `name`, `description`, `main_image`,
  `price`, `original_price`, `stock`, `box_price`, `min_order_count`, `status`,
  `is_recommended`, `is_hot`, `no_member_discount`, `no_cooking`, `sort_order`, `sales_count`
)
SELECT s.`id`, c.`id`, 'SH02-ZhaoPaiMian-JiZaMian', '鸡杂面', '二店测试鸡杂面',
  '/uploads/products/jizamian.webp', 9.00, NULL, 9999, 1.00, 1, 'ON_SALE',
  0, 0, 0, 0, 1, 0
FROM `stores` s
JOIN `product_categories` c ON c.`store_id` = s.`id` AND c.`category_no` = 'SH02-ZhaoPaiMian'
WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `products` p WHERE p.`store_id` = s.`id` AND p.`product_no` = 'SH02-ZhaoPaiMian-JiZaMian');

INSERT INTO `products` (
  `store_id`, `category_id`, `product_no`, `name`, `description`, `main_image`,
  `price`, `original_price`, `stock`, `box_price`, `min_order_count`, `status`,
  `is_recommended`, `is_hot`, `no_member_discount`, `no_cooking`, `sort_order`, `sales_count`
)
SELECT s.`id`, c.`id`, 'SH02-GanBanMian-GanBanKuanMian', '干拌宽面', '二店测试干拌宽面',
  '/uploads/products/ganbankuanmian.webp', 8.00, NULL, 9999, 1.00, 1, 'ON_SALE',
  0, 0, 0, 0, 0, 0
FROM `stores` s
JOIN `product_categories` c ON c.`store_id` = s.`id` AND c.`category_no` = 'SH02-GanBanMian'
WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `products` p WHERE p.`store_id` = s.`id` AND p.`product_no` = 'SH02-GanBanMian-GanBanKuanMian');

INSERT INTO `products` (
  `store_id`, `category_id`, `product_no`, `name`, `description`, `main_image`,
  `price`, `original_price`, `stock`, `box_price`, `min_order_count`, `status`,
  `is_recommended`, `is_hot`, `no_member_discount`, `no_cooking`, `sort_order`, `sales_count`
)
SELECT s.`id`, c.`id`, 'SH02-YinPin-SuanMeiTang', '酸梅汤', '二店测试酸梅汤',
  NULL, 3.00, NULL, 9999, 0.00, 1, 'ON_SALE',
  0, 0, 1, 1, 0, 0
FROM `stores` s
JOIN `product_categories` c ON c.`store_id` = s.`id` AND c.`category_no` = 'SH02-YinPin'
WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `products` p WHERE p.`store_id` = s.`id` AND p.`product_no` = 'SH02-YinPin-SuanMeiTang');

-- 面类挂规格
INSERT INTO `product_option_groups` (`product_id`, `group_id`, `sort_order`)
SELECT p.`id`, g.`id`, g.`sort_order`
FROM `products` p
JOIN `stores` s ON s.`id` = p.`store_id` AND s.`store_no` = 'SH02'
JOIN `option_groups` g ON g.`store_id` = s.`id` AND g.`code` IN ('SH02-Liang', 'SH02-La')
WHERE (p.`product_no` LIKE 'SH02-ZhaoPaiMian-%' OR p.`product_no` LIKE 'SH02-GanBanMian-%')
  AND NOT EXISTS (
    SELECT 1 FROM `product_option_groups` pog
    WHERE pog.`product_id` = p.`id` AND pog.`group_id` = g.`id`
  );

-- -----------------------------------------------------------------------------
-- 二店桌台（WM 外带 + 两张堂食；qr_token 仅本地测试用）
-- -----------------------------------------------------------------------------
INSERT INTO `tables` (
  `store_id`, `table_no`, `table_name`, `qr_token`,
  `wechat_qr_url`, `alipay_qr_url`, `status`, `created_at`, `updated_at`
)
SELECT s.`id`, 'WM', '外带自取', 'SH02-TEST-WM-TOKEN-00000000000000001',
  NULL, NULL, 1, NOW(3), NOW(3)
FROM `stores` s WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `tables` t WHERE t.`store_id` = s.`id` AND t.`table_no` = 'WM');

INSERT INTO `tables` (
  `store_id`, `table_no`, `table_name`, `qr_token`,
  `wechat_qr_url`, `alipay_qr_url`, `status`, `created_at`, `updated_at`
)
SELECT s.`id`, 'B01', '1号桌', 'SH02-TEST-B01-TOKEN-0000000000000001',
  NULL, NULL, 1, NOW(3), NOW(3)
FROM `stores` s WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `tables` t WHERE t.`store_id` = s.`id` AND t.`table_no` = 'B01');

INSERT INTO `tables` (
  `store_id`, `table_no`, `table_name`, `qr_token`,
  `wechat_qr_url`, `alipay_qr_url`, `status`, `created_at`, `updated_at`
)
SELECT s.`id`, 'B02', '2号桌', 'SH02-TEST-B02-TOKEN-0000000000000002',
  NULL, NULL, 1, NOW(3), NOW(3)
FROM `stores` s WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (SELECT 1 FROM `tables` t WHERE t.`store_id` = s.`id` AND t.`table_no` = 'B02');

-- -----------------------------------------------------------------------------
-- 二店测试摄像头（管理端门店筛选联调）
-- -----------------------------------------------------------------------------
INSERT INTO `cameras` (
  `store_id`, `name`, `camera_type`, `source_url`, `fps`, `detect_fps`, `status`, `created_at`, `updated_at`
)
SELECT s.`id`, '二店门口测试摄像', 'USB', '0', 25, 3, 'OFFLINE', NOW(3), NOW(3)
FROM `stores` s WHERE s.`store_no` = 'SH02'
  AND NOT EXISTS (
    SELECT 1 FROM `cameras` c WHERE c.`store_id` = s.`id` AND c.`name` = '二店门口测试摄像'
  );

-- -----------------------------------------------------------------------------
-- 校验摘要
-- -----------------------------------------------------------------------------
SELECT
  (SELECT COUNT(*) FROM `users` WHERE `store_id` = (SELECT `id` FROM `stores` WHERE `store_no` = 'SH02')) AS sh02_users,
  (SELECT COUNT(*) FROM `product_categories` WHERE `store_id` = (SELECT `id` FROM `stores` WHERE `store_no` = 'SH02')) AS sh02_categories,
  (SELECT COUNT(*) FROM `products` WHERE `store_id` = (SELECT `id` FROM `stores` WHERE `store_no` = 'SH02')) AS sh02_products,
  (SELECT COUNT(*) FROM `tables` WHERE `store_id` = (SELECT `id` FROM `stores` WHERE `store_no` = 'SH02')) AS sh02_tables;

SET FOREIGN_KEY_CHECKS = 1;
-- END LOCAL TEST SEED

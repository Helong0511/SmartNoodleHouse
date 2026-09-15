-- =============================================================================
-- 射洪·肠旺面(总店 SH01) 菜单扩展种子数据
-- 依据 smart_noodle_house.sql 表结构生成；门店 store_no='SH01'（射洪，经纬度 30.8719/105.3884）
-- 内容：新增 5 个分类、新增规格组（含姜末/面型/口感/汤汁/加臊子/甜度/包子个数等）、
--       14 道餐品、商品-规格组绑定、商品主图。
-- 幂等：所有写入均带 NOT EXISTS，可重复执行。
-- 图片：1200×1200 方图，存放路径 /uploads/products/，文件名与下方 main_image 一致。
-- =============================================================================

SET NAMES utf8mb4;
USE smart_noodle_house;

-- ============================================================================
-- 一、新增商品分类（沿用已有 6 个分类：特价品/绵阳米粉/面/小吃/卤味/饮料）
--     sort_order 从 6 开始往后排，避免与已有分类冲突
-- ============================================================================
INSERT INTO product_categories (store_id, category_no, name, description, sort_order, status, created_at, updated_at) VALUES
(1, 'SH01-ZhaoPaiMian', '招牌面·汤面', '射洪招牌汤面：鸡杂/杂酱/豌杂/笋子牛肉/蹄花/肠旺面', 6, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, 'SH01-GanBanMian', '干拌面', '干拌宽面、烧椒面（可切汤/干拌）', 7, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, 'SH01-ChaoShouShuiJiao', '抄手·水饺', '清汤/红油抄手与红油水饺', 8, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, 'SH01-TangYuan', '甜香汤圆', '汤圆荷包蛋、红糖小汤圆（甜品，咸甜分开）', 9, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(1, 'SH01-BaoZiDianXin', '包子·点心', '香葱包、酱肉包，支持单个起售', 10, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- ============================================================================
-- 二、新增规格组（原有 1-7：量/加料/辣/麻/葱/香菜/蒜末）
--     本次新增 8-20：姜末、面型、口感、汤汁、加臊子、加蛋加菜、汤底、皮薄厚、甜度、
--     荷包蛋熟度、汤圆数量、包子个数、是否加热
--     store_id=NULL 表示总部共享，全店可绑（与原有 0000- 编码一致）
-- ============================================================================
INSERT INTO option_groups (id, store_id, name, code, selection_type, is_required, sort_order, status, created_at, updated_at) VALUES
(8,  NULL, '姜末', '0000-JiangMo', 'SINGLE', 0, 7, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(9,  NULL, '面型', '0000-MianXing', 'SINGLE', 0, 8, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(10, NULL, '口感', '0000-KouGan', 'SINGLE', 0, 9, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(11, NULL, '汤汁', '0000-TangZhi', 'SINGLE', 0, 10, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(12, NULL, '加臊子', '0000-JiaSaoZi', 'MULTIPLE', 0, 11, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(13, NULL, '加蛋加菜', '0000-JiaDanJiaCai', 'MULTIPLE', 0, 12, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(14, NULL, '汤底', '0000-TangDi', 'SINGLE', 0, 13, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(15, NULL, '皮薄厚', '0000-PiBaoHou', 'SINGLE', 0, 14, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(16, NULL, '甜度', '0000-TianDu', 'SINGLE', 0, 15, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(17, NULL, '荷包蛋熟度', '0000-ShuDu', 'SINGLE', 0, 16, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(18, NULL, '汤圆数量', '0000-TangYuanShuLiang', 'SINGLE', 1, 17, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(19, NULL, '包子个数', '0000-BaoZiGeShu', 'SINGLE', 1, 18, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(20, NULL, '是否加热', '0000-ShiFouJiaRe', 'SINGLE', 1, 19, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- ============================================================================
-- 三、新增规格选项
-- ============================================================================

-- 3.1 姜末（group 8）★ 用户要求的葱/姜/蒜之“姜”
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(8, '不加姜末', '0000-JiangMo-BuJiaJiangMo', 0.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(8, '微量姜末', '0000-JiangMo-WeiLiangJiangMo', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(8, '正常姜末', '0000-JiangMo-ZhengChangJiangMo', 0.00, 2, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(8, '加量姜末', '0000-JiangMo-JiaLiangJiangMo', 0.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.2 面型（group 9）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(9, '细面', '0000-MianXing-XiMian', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(9, '宽面', '0000-MianXing-KuanMian', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(9, '韭菜叶', '0000-MianXing-JiuCaiYe', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(9, '碱水面', '0000-MianXing-JianShuiMian', 0.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.3 口感（group 10）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(10, '软一点', '0000-KouGan-RuanYiDian', 0.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(10, '标准', '0000-KouGan-BiaoZhun', 0.00, 1, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(10, '硬一点(提黄)', '0000-KouGan-YingYiDian', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.4 汤汁（group 11）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(11, '正常汤', '0000-TangZhi-ZhengChangTang', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(11, '多汤', '0000-TangZhi-DuoTang', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(11, '少汤', '0000-TangZhi-ShaoTang', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(11, '干拌(免汤)', '0000-TangZhi-GanBan', 0.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.5 加臊子（group 12，多选加价；牛肉/肥肠沿用原 group 2 加料）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(12, '加杂酱', '0000-JiaSaoZi-ZaJiang', 3.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(12, '加鸡杂', '0000-JiaSaoZi-JiZa', 4.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(12, '加蹄花', '0000-JiaSaoZi-TiHua', 6.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(12, '加血旺', '0000-JiaSaoZi-XueWang', 3.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.6 加蛋加菜（group 13）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(13, '加煎蛋', '0000-JiaDanJiaCai-JianDan', 2.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(13, '加荷包蛋', '0000-JiaDanJiaCai-HeBaoDan', 2.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(13, '加豌豆', '0000-JiaDanJiaCai-WanDou', 1.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(13, '加青菜', '0000-JiaDanJiaCai-QingCai', 1.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(13, '加豆芽', '0000-JiaDanJiaCai-DouYa', 1.00, 4, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.7 汤底（group 14，抄手/水饺）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(14, '清汤', '0000-TangDi-QingTang', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(14, '红油', '0000-TangDi-HongYou', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(14, '酸辣', '0000-TangDi-SuanLa', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(14, '干拌', '0000-TangDi-GanBan', 0.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.8 皮薄厚（group 15）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(15, '标准', '0000-PiBaoHou-BiaoZhun', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(15, '皮薄', '0000-PiBaoHou-PiBao', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.9 甜度（group 16，汤圆）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(16, '正常糖', '0000-TianDu-ZhengChangTang', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(16, '少糖', '0000-TianDu-ShaoTang', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(16, '无糖', '0000-TianDu-WuTang', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.10 荷包蛋熟度（group 17）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(17, '溏心', '0000-ShuDu-TangXin', 0.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(17, '全熟', '0000-ShuDu-QuanShu', 0.00, 1, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.11 汤圆数量（group 18，必选，默认 6 个）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(18, '4个', '0000-TangYuanShuLiang-4Ge', 0.00, 0, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(18, '6个', '0000-TangYuanShuLiang-6Ge', 0.00, 1, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(18, '8个', '0000-TangYuanShuLiang-8Ge', 0.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.12 包子个数（group 19；基础价=1个价，其余为相对加价）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(19, '1个', '0000-BaoZiGeShu-1Ge', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(19, '2个', '0000-BaoZiGeShu-2Ge', 2.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(19, '半笼(3个)', '0000-BaoZiGeShu-BanLong', 4.00, 2, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(19, '一笼(6个)', '0000-BaoZiGeShu-YiLong', 10.00, 3, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

-- 3.13 是否加热（group 20）
INSERT INTO options (group_id, name, code, extra_price, sort_order, is_default, status, created_at, updated_at) VALUES
(20, '现蒸', '0000-ShiFouJiaRe-XianZheng', 0.00, 0, 1, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888'),
(20, '复蒸加热', '0000-ShiFouJiaRe-FuZheng', 0.00, 1, 0, 1, '2026-08-18 18:58:58.888', '2026-08-18 18:58:58.888');

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
JOIN option_groups g ON g.code IN ('0000-Liang','0000-JiaLiao','0000-La','0000-Ma','0000-Cong','0000-XiangCai','0000-SuanMo','0000-JiangMo','0000-MianXing','0000-KouGan','0000-TangZhi','0000-JiaSaoZi','0000-JiaDanJiaCai')
WHERE p.store_id = (SELECT id FROM stores WHERE store_no = 'SH01')
  AND c.category_no IN ('SH01-ZhaoPaiMian','SH01-GanBanMian')
  AND NOT EXISTS (SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id);

-- 5.2 抄手·水饺：量(份)/加料/辣/麻/葱/香菜/蒜末/姜末/加蛋加菜/汤底/皮薄厚
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, g.sort_order
FROM products p
JOIN product_categories c ON c.id = p.category_id
JOIN option_groups g ON g.code IN ('0000-Liang','0000-JiaLiao','0000-La','0000-Ma','0000-Cong','0000-XiangCai','0000-SuanMo','0000-JiangMo','0000-JiaDanJiaCai','0000-TangDi','0000-PiBaoHou')
WHERE p.store_id = (SELECT id FROM stores WHERE store_no = 'SH01')
  AND c.category_no = 'SH01-ChaoShouShuiJiao'
  AND NOT EXISTS (SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id);

-- 5.3 汤圆荷包蛋：甜度 + 荷包蛋熟度 + 汤圆数量
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, g.sort_order
FROM products p
JOIN option_groups g ON g.code IN ('0000-TianDu','0000-ShuDu','0000-TangYuanShuLiang')
WHERE p.store_id = (SELECT id FROM stores WHERE store_no = 'SH01')
  AND p.product_no = 'SH01-TangYuan-TangYuanHeBaoDan'
  AND NOT EXISTS (SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id);

-- 5.4 红糖小汤圆：甜度 + 汤圆数量
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, g.sort_order
FROM products p
JOIN option_groups g ON g.code IN ('0000-TianDu','0000-TangYuanShuLiang')
WHERE p.store_id = (SELECT id FROM stores WHERE store_no = 'SH01')
  AND p.product_no = 'SH01-TangYuan-HongTangXiaoTangYuan'
  AND NOT EXISTS (SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id);

-- 5.5 包子·点心：包子个数 + 是否加热
INSERT INTO product_option_groups (product_id, group_id, sort_order)
SELECT p.id, g.id, g.sort_order
FROM products p
JOIN product_categories c ON c.id = p.category_id
JOIN option_groups g ON g.code IN ('0000-BaoZiGeShu','0000-ShiFouJiaRe')
WHERE p.store_id = (SELECT id FROM stores WHERE store_no = 'SH01')
  AND c.category_no = 'SH01-BaoZiDianXin'
  AND NOT EXISTS (SELECT 1 FROM product_option_groups pog WHERE pog.product_id = p.id AND pog.group_id = g.id);

-- ============================================================================
-- 六、商品主图落库（与 products.main_image 一致，1200×1200 方图）
-- ============================================================================
INSERT INTO product_images (product_id, image_url, image_type, sort_order)
SELECT p.id, p.main_image, 'MAIN', 0
FROM products p
WHERE p.store_id = (SELECT id FROM stores WHERE store_no = 'SH01')
  AND p.product_no IN (
    'SH01-ZhaoPaiMian-JiZaMian','SH01-ZhaoPaiMian-ZaJiangMian','SH01-ZhaoPaiMian-WanZaMian',
    'SH01-ZhaoPaiMian-SunZiNiuRouMian','SH01-ZhaoPaiMian-TiHuaMian','SH01-ZhaoPaiMian-ChangWangMian',
    'SH01-GanBanMian-GanBanKuanMian','SH01-GanBanMian-ShaoJiaoMian',
    'SH01-ChaoShouShuiJiao-ChaoShou','SH01-ChaoShouShuiJiao-HongYouShuiJiao',
    'SH01-TangYuan-TangYuanHeBaoDan','SH01-TangYuan-HongTangXiaoTangYuan',
    'SH01-BaoZiDianXin-XiangCongBao','SH01-BaoZiDianXin-JiangRouBao'
  )
  AND NOT EXISTS (SELECT 1 FROM product_images pi WHERE pi.product_id = p.id AND pi.image_url = p.main_image);

-- ============================================================================
-- 七、校验查询
-- ============================================================================
SELECT
  (SELECT COUNT(*) FROM product_categories WHERE store_id = 1) AS categories,
  (SELECT COUNT(*) FROM option_groups) AS option_groups,
  (SELECT COUNT(*) FROM options) AS options,
  (SELECT COUNT(*) FROM products WHERE store_id = 1) AS products,
  (SELECT COUNT(*) FROM product_option_groups) AS product_option_bindings;

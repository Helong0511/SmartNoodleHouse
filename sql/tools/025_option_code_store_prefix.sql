-- 规格编码规范补齐（可重复执行）
-- 总部：0000-{逻辑}；门店：{store_no}-{逻辑}；选项：{前缀}-{组逻辑}-{选项逻辑}
-- 例：0000-Liang / SH01-Liang / SH01-Liang-XiaoWan

SET NAMES utf8mb4;

-- 1) 总部共享组补 0000-
UPDATE option_groups
SET code = CONCAT('0000-', code)
WHERE store_id IS NULL
  AND code NOT LIKE '0000-%';

-- 2) 门店私有组：统一为 {store_no}-{逻辑段}
UPDATE option_groups g
INNER JOIN stores s ON s.id = g.store_id
SET g.code = CONCAT(
  s.store_no,
  '-',
  CASE
    WHEN g.code LIKE '0000-%' THEN SUBSTRING(g.code FROM 6)
    WHEN g.code LIKE CONCAT(s.store_no, '-%') THEN SUBSTRING(g.code FROM CHAR_LENGTH(s.store_no) + 2)
    ELSE g.code
  END
)
WHERE g.store_id IS NOT NULL
  AND g.code NOT LIKE CONCAT(s.store_no, '-%');

-- 3) 总部选项：无 0000- 前缀时补上（如 Liang-XiaoWan → 0000-Liang-XiaoWan）
UPDATE options o
INNER JOIN option_groups g ON g.id = o.group_id
SET o.code = CONCAT('0000-', o.code)
WHERE g.store_id IS NULL
  AND g.code LIKE '0000-%'
  AND o.code NOT LIKE '0000-%';

-- 4) 门店选项：改为 {store_no}-…（若仍是 0000- 开头则换前缀）
UPDATE options o
INNER JOIN option_groups g ON g.id = o.group_id
INNER JOIN stores s ON s.id = g.store_id
SET o.code = CONCAT(
  s.store_no,
  '-',
  CASE
    WHEN o.code LIKE '0000-%' THEN SUBSTRING(o.code FROM 6)
    WHEN o.code LIKE CONCAT(s.store_no, '-%') THEN SUBSTRING(o.code FROM CHAR_LENGTH(s.store_no) + 2)
    ELSE o.code
  END
)
WHERE g.store_id IS NOT NULL
  AND o.code NOT LIKE CONCAT(s.store_no, '-%');

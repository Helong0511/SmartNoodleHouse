-- P1：同一收银员仅允许一个 OPEN 班次（生成列 + 唯一索引）
-- 可在已有库上单独执行；新库请直接使用更新后的 001_schema.sql

-- 若列已存在会报错，可忽略后继续加索引
ALTER TABLE cashier_shifts
  ADD COLUMN open_operator_key BIGINT UNSIGNED
    GENERATED ALWAYS AS (IF(status = 'OPEN', operator_id, NULL)) STORED
    COMMENT 'OPEN 时为 operator_id，CLOSED 为 NULL；用于唯一约束';

-- 清理历史重复 OPEN（保留最大 id）
UPDATE cashier_shifts cs
INNER JOIN (
  SELECT operator_id, MAX(id) AS keep_id
  FROM cashier_shifts
  WHERE status = 'OPEN'
  GROUP BY operator_id
  HAVING COUNT(*) > 1
) d ON d.operator_id = cs.operator_id
SET cs.status = 'CLOSED',
    cs.closed_at = COALESCE(cs.closed_at, NOW(3)),
    cs.remark = CONCAT(COALESCE(cs.remark, ''), ' [auto-close duplicate OPEN]')
WHERE cs.status = 'OPEN' AND cs.id <> d.keep_id;

ALTER TABLE cashier_shifts
  ADD UNIQUE INDEX uk_cashier_shift_one_open (open_operator_key);

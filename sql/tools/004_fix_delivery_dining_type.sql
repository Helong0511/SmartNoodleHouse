-- ============================================================================
-- 修复历史外卖订单 dining_type
-- 适用：channel 为 MEITUAN / ELEME 但仍为默认 DINE_IN 的订单
-- 新单已在 delivery_service 落库为 TAKEAWAY；本脚本幂等可重复执行
-- ============================================================================

SET NAMES utf8mb4;

UPDATE orders
SET dining_type = 'TAKEAWAY'
WHERE channel IN ('MEITUAN', 'ELEME')
  AND (dining_type IS NULL OR dining_type = '' OR dining_type = 'DINE_IN');

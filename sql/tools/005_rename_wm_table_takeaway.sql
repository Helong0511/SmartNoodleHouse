-- 将种子虚拟桌「外卖订单」更名为「外带自取」，避免顾客端显示成桌号
UPDATE tables
SET table_name = '外带自取'
WHERE table_no = 'WM'
  AND table_name = '外卖订单';

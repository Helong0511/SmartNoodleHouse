-- 清理历史重复 MAIN 图（换主图后详情轮播仍展示旧图）
-- 保留与 products.main_image 一致的 MAIN 记录，幂等可重复执行

DELETE pi
FROM product_images pi
INNER JOIN products p ON p.id = pi.product_id
WHERE pi.image_type = 'MAIN'
  AND (p.main_image IS NULL OR pi.image_url <> p.main_image);

-- 分类分时段排序方案
-- 幂等：表已存在则跳过

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

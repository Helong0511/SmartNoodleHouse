"""分类分时段排序：命中 / 重叠 / 跨午夜 / 回退默认"""
from datetime import time

from app.models import CategorySortSchedule, ProductCategory
from app.services.category_schedule import (
    pick_active_schedule,
    sort_categories_by_schedule,
    time_in_window,
)


def test_time_in_window_normal_and_overnight():
    assert time_in_window(time(9, 0), time(7, 0), time(10, 30)) is True
    assert time_in_window(time(10, 30), time(7, 0), time(10, 30)) is False
    assert time_in_window(time(6, 59), time(7, 0), time(10, 30)) is False
    # 跨午夜 22:00–06:00
    assert time_in_window(time(23, 0), time(22, 0), time(6, 0)) is True
    assert time_in_window(time(5, 0), time(22, 0), time(6, 0)) is True
    assert time_in_window(time(12, 0), time(22, 0), time(6, 0)) is False
    # start==end 全天
    assert time_in_window(time(3, 0), time(0, 0), time(0, 0)) is True


def test_pick_active_schedule_priority():
    low = CategorySortSchedule(
        id=1,
        store_id=1,
        name="低",
        start_time=time(8, 0),
        end_time=time(12, 0),
        priority=1,
        status=1,
    )
    high = CategorySortSchedule(
        id=2,
        store_id=1,
        name="高",
        start_time=time(9, 0),
        end_time=time(11, 0),
        priority=10,
        status=1,
    )
    assert pick_active_schedule([low, high], time(9, 30)) is high
    assert pick_active_schedule([low, high], time(11, 30)) is low
    assert pick_active_schedule([low, high], time(7, 0)) is None


def test_sort_categories_by_schedule_partial():
    a = ProductCategory(id=1, store_id=1, name="早点", sort_order=2, status=1)
    b = ProductCategory(id=2, store_id=1, name="面", sort_order=0, status=1)
    c = ProductCategory(id=3, store_id=1, name="卤味", sort_order=1, status=1)
    # 仅配置早点、卤味；面未列入应跟在后面且保持默认序
    ordered = sort_categories_by_schedule([a, b, c], {1: 0, 3: 1})
    assert [x.id for x in ordered] == [1, 3, 2]


def test_sort_fallback_when_empty_items():
    a = ProductCategory(id=1, store_id=1, name="早点", sort_order=2, status=1)
    b = ProductCategory(id=2, store_id=1, name="面", sort_order=0, status=1)
    ordered = sort_categories_by_schedule([a, b], {})
    assert [x.id for x in ordered] == [2, 1]

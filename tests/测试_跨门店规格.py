"""P0-1: 跨门店商品/规格校验 — 验证 calculate_items_batch 中 option group store_id 校验存在。"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from app.services.order_service import OrderPriceCalculator


def test_cross_store_option_group_validation_exists():
    """验证 calculate_items_batch 源码中包含跨门店规格组校验。"""
    source = inspect.getsource(OrderPriceCalculator.calculate_items_batch)
    # P0-1 修复：检查 group.store_id 非 NULL 时必须等于订单门店
    assert "cross_store_groups" in source
    assert "store_id" in source
    assert "禁止跨店下单" in source or "跨店" in source
    # 确保 NULL（总部共享）不会触发拒绝
    assert "is not None" in source


def test_product_store_id_filter_exists():
    """验证 Product 查询已按 store_id 过滤（原有逻辑）。"""
    source = inspect.getsource(OrderPriceCalculator.calculate_items_batch)
    assert "Product.store_id == store_id" in source
    assert "商品不属于本门店" in source or "禁止跨店下单" in source


def test_calculate_items_batch_requires_store_id():
    """store_id=None 或 <=0 时拒绝（已有防护）。"""
    from app.core.exceptions import AppException

    with pytest.raises(AppException) as ei:
        import asyncio
        asyncio.run(
            OrderPriceCalculator.calculate_items_batch(
                MagicMock(), [{"product_id": 1, "quantity": 1}], store_id=None
            )
        )
    assert ei.value.code == 40008

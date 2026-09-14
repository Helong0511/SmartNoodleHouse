"""跨店商品：门店 A 订单不得购买门店 B 商品。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import AppException


@pytest.mark.asyncio
async def test_cross_store_product_rejected():
    from app.services.order_service import OrderPriceCalculator

    store_a, store_b = 1, 2
    product_b = SimpleNamespace(id=99, store_id=store_b, status="ON_SALE", name="B店面")

    # 本店查询为空，任意门店查询命中 B 店商品 → 跨店
    empty_result = MagicMock()
    empty_result.scalars.return_value = []
    any_result = MagicMock()
    any_result.scalars.return_value = [product_b]

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[empty_result, any_result])

    with pytest.raises(AppException) as ei:
        await OrderPriceCalculator.calculate_items_batch(
            db,
            [{"product_id": 99, "quantity": 1, "option_ids": []}],
            store_id=store_a,
        )
    assert ei.value.code == 40002
    assert "跨店" in ei.value.message or "不属于本门店" in ei.value.message


@pytest.mark.asyncio
async def test_missing_store_id_forbidden():
    from app.services.order_service import OrderPriceCalculator

    db = AsyncMock()
    with pytest.raises(AppException) as ei:
        await OrderPriceCalculator.calculate_items_batch(
            db,
            [{"product_id": 1, "quantity": 1}],
            store_id=None,
        )
    assert ei.value.code == 40008

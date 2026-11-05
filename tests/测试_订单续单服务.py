"""订单复购服务回归测试。"""
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import Order, OrderItem, Product, ProductCategory, Store
from app.services.dining_session_service import DiningSessionService
from app.core.response import naive_local_now
from app.services.order_service import OrderReorderService


@pytest.mark.asyncio
async def test_reorder_prices_items_in_order_store(db_session):
    """复购计价必须带上原订单门店，否则餐品会被全部跳过。"""
    store = Store(id=901, store_no="REORDER-STORE", name="复购测试店", status=1)
    category = ProductCategory(
        id=902,
        store_id=store.id,
        category_no="REORDER-CAT",
        name="面类",
        status=1,
    )
    product = Product(
        id=903,
        store_id=store.id,
        category_id=category.id,
        product_no="REORDER-PRODUCT",
        name="牛肉面",
        price=Decimal("18.00"),
        status="ON_SALE",
    )
    order = Order(
        id=904,
        order_no="REORDER-ORDER",
        customer_id=1,
        store_id=store.id,
        status="COMPLETED",
    )
    item = OrderItem(
        id=905,
        order_id=order.id,
        product_id=product.id,
        product_name_snapshot=product.name,
        unit_price_snapshot=product.price,
        quantity=2,
        subtotal=Decimal("36.00"),
    )
    db_session.add_all([store, category, product, order, item])
    await db_session.flush()

    result = await OrderReorderService.build_cart_from_order(db_session, order)

    assert result["cart_items"] == [
        {
            "product_id": product.id,
            "name": product.name,
            "quantity": 2,
            "option_ids": [],
            "option_labels": [],
            "option_text": "",
            "unit_price": "18.00",
            "subtotal": "36.00",
            "imageUrl": "",
            "main_image": "",
            "no_member_discount": False,
        }
    ]
    assert result["store_id"] == store.id


@pytest.mark.asyncio
async def test_dining_session_touch_accepts_naive_mysql_datetime():
    """MySQL DATETIME 读回 naive 时，触碰就餐会话不能抛时区比较异常。"""
    session = SimpleNamespace(
        status="ACTIVE",
        # MySQL DATETIME 无时区值按应用时区解释，而不是 CI 主机 UTC。
        expires_at=naive_local_now() + timedelta(hours=1),
        last_active_at=None,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=session), flush=AsyncMock())

    await DiningSessionService.touch(db, 1)

    assert session.last_active_at is not None
    db.flush.assert_awaited_once()

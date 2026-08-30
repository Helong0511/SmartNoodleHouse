"""免烹饪订单直出餐校验"""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.exceptions import AppException
from app.models import Order, OrderItem, Product, ProductCategory, Store
from app.services.order_lifecycle import apply_order_status, order_all_no_cooking


@pytest.mark.asyncio
async def test_order_all_no_cooking(db_session):
    store = Store(id=901, store_no="T-NC-01", name="测试店", status=1)
    cat = ProductCategory(id=902, store_id=store.id, name="卤味", sort_order=0, status=1)
    db_session.add_all([store, cat])
    await db_session.flush()

    cook = Product(
        id=903,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-COOK",
        name="牛肉面",
        price=Decimal("18.00"),
        no_cooking=False,
    )
    ready = Product(
        id=904,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-READY",
        name="卤蛋",
        price=Decimal("3.00"),
        no_cooking=True,
    )
    db_session.add_all([cook, ready])
    await db_session.flush()

    pure = Order(id=905, order_no="O-PURE", store_id=store.id, status="PAID", subtotal=Decimal("3.00"), total_amount=Decimal("3.00"))
    mixed = Order(id=906, order_no="O-MIX", store_id=store.id, status="PAID", subtotal=Decimal("21.00"), total_amount=Decimal("21.00"))
    db_session.add_all([pure, mixed])
    await db_session.flush()
    db_session.add_all(
        [
            OrderItem(id=907, order_id=pure.id, product_id=ready.id, product_name_snapshot="卤蛋", unit_price_snapshot=Decimal("3.00"), quantity=1, subtotal=Decimal("3.00")),
            OrderItem(id=908, order_id=mixed.id, product_id=cook.id, product_name_snapshot="牛肉面", unit_price_snapshot=Decimal("18.00"), quantity=1, subtotal=Decimal("18.00")),
            OrderItem(id=909, order_id=mixed.id, product_id=ready.id, product_name_snapshot="卤蛋", unit_price_snapshot=Decimal("3.00"), quantity=1, subtotal=Decimal("3.00")),
        ]
    )
    await db_session.flush()

    assert await order_all_no_cooking(db_session, pure.id) is True
    assert await order_all_no_cooking(db_session, mixed.id) is False


@pytest.mark.asyncio
async def test_staff_direct_complete_rejected_for_mixed_order(db_session):
    store = Store(id=911, store_no="T-NC-02", name="测试店2", status=1)
    cat = ProductCategory(id=912, store_id=store.id, name="卤味", sort_order=0, status=1)
    db_session.add_all([store, cat])
    await db_session.flush()

    cook = Product(
        id=913,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-NOODLE",
        name="面",
        price=Decimal("18.00"),
        no_cooking=False,
    )
    ready = Product(
        id=914,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-EGG",
        name="卤蛋",
        price=Decimal("3.00"),
        no_cooking=True,
    )
    db_session.add_all([cook, ready])
    await db_session.flush()

    mixed = Order(id=915, order_no="O-MIX2", store_id=store.id, status="PAID", subtotal=Decimal("21.00"), total_amount=Decimal("21.00"))
    db_session.add(mixed)
    await db_session.flush()
    db_session.add_all(
        [
            OrderItem(id=916, order_id=mixed.id, product_id=cook.id, product_name_snapshot="面", unit_price_snapshot=Decimal("18.00"), quantity=1, subtotal=Decimal("18.00")),
            OrderItem(id=917, order_id=mixed.id, product_id=ready.id, product_name_snapshot="卤蛋", unit_price_snapshot=Decimal("3.00"), quantity=1, subtotal=Decimal("3.00")),
        ]
    )
    await db_session.flush()

    with pytest.raises(AppException) as exc:
        await apply_order_status(db_session, mixed, "COMPLETED", source="STAFF", assert_kind="STAFF")
    assert exc.value.code == 40026

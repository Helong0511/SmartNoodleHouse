"""免烹饪订单直出餐校验"""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.exceptions import AppException
from app.models import Order, OrderItem, Product, ProductCategory, Store
from app.services.order_lifecycle import apply_order_status, order_all_no_cooking


@pytest.fixture(autouse=True)
def _skip_order_side_effects(monkeypatch):
    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr("app.services.order_lifecycle.record_order_status", noop)
    monkeypatch.setattr("app.services.order_lifecycle.notify_order_ready_subscribe", noop)


@pytest.mark.asyncio
async def test_order_all_no_cooking(db_session):
    store = Store(id=901, store_no="T-NC-01", name="测试店", status=1)
    cat = ProductCategory(id=902, category_no="C902", store_id=store.id, name="卤味", sort_order=0, status=1)
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
    cat = ProductCategory(id=912, category_no="C912", store_id=store.id, name="卤味", sort_order=0, status=1)
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

@pytest.mark.asyncio
async def test_staff_direct_complete_rejected_for_pure_no_cooking(db_session):
    store = Store(id=941, store_no="T-NC-05", name="测试店5", status=1)
    cat = ProductCategory(id=942, category_no="C942", store_id=store.id, name="卤味", sort_order=0, status=1)
    db_session.add_all([store, cat])
    await db_session.flush()

    ready = Product(
        id=943,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-EGG4",
        name="卤蛋",
        price=Decimal("3.00"),
        no_cooking=True,
    )
    db_session.add(ready)
    await db_session.flush()

    pure = Order(
        id=944,
        order_no="O-PURE3",
        store_id=store.id,
        status="PAID",
        subtotal=Decimal("3.00"),
        total_amount=Decimal("3.00"),
    )
    db_session.add(pure)
    await db_session.flush()
    db_session.add(
        OrderItem(
            id=945,
            order_id=pure.id,
            product_id=ready.id,
            product_name_snapshot="卤蛋",
            unit_price_snapshot=Decimal("3.00"),
            quantity=1,
            subtotal=Decimal("3.00"),
        )
    )
    await db_session.flush()

    with pytest.raises(AppException) as exc:
        await apply_order_status(db_session, pure, "COMPLETED", source="STAFF", assert_kind="STAFF")
    assert exc.value.code == 40026


@pytest.mark.asyncio
async def test_no_cooking_ready_then_completed(db_session):
    store = Store(id=951, store_no="T-NC-06", name="测试店6", status=1)
    cat = ProductCategory(id=952, category_no="C952", store_id=store.id, name="卤味", sort_order=0, status=1)
    db_session.add_all([store, cat])
    await db_session.flush()

    ready = Product(
        id=953,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-EGG5",
        name="卤蛋",
        price=Decimal("3.00"),
        no_cooking=True,
    )
    db_session.add(ready)
    await db_session.flush()

    pure = Order(
        id=954,
        order_no="O-PURE4",
        store_id=store.id,
        status="PAID",
        subtotal=Decimal("3.00"),
        total_amount=Decimal("3.00"),
    )
    db_session.add(pure)
    await db_session.flush()
    db_session.add(
        OrderItem(
            id=955,
            order_id=pure.id,
            product_id=ready.id,
            product_name_snapshot="卤蛋",
            unit_price_snapshot=Decimal("3.00"),
            quantity=1,
            subtotal=Decimal("3.00"),
        )
    )
    await db_session.flush()

    pure = await apply_order_status(db_session, pure, "READY", source="STAFF", assert_kind="STAFF")
    assert pure.status == "READY"
    pure = await apply_order_status(db_session, pure, "COMPLETED", source="STAFF", assert_kind="STAFF")
    assert pure.status == "COMPLETED"


@pytest.mark.asyncio
async def test_staff_direct_ready_for_pure_no_cooking_order(db_session):
    store = Store(id=921, store_no="T-NC-03", name="测试店3", status=1)
    cat = ProductCategory(id=922, category_no="C922", store_id=store.id, name="卤味", sort_order=0, status=1)
    db_session.add_all([store, cat])
    await db_session.flush()

    ready = Product(
        id=923,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-EGG2",
        name="卤蛋",
        price=Decimal("3.00"),
        no_cooking=True,
    )
    db_session.add(ready)
    await db_session.flush()

    pure = Order(
        id=924,
        order_no="O-PURE2",
        store_id=store.id,
        status="PAID",
        subtotal=Decimal("3.00"),
        total_amount=Decimal("3.00"),
    )
    db_session.add(pure)
    await db_session.flush()
    db_session.add(
        OrderItem(
            id=925,
            order_id=pure.id,
            product_id=ready.id,
            product_name_snapshot="卤蛋",
            unit_price_snapshot=Decimal("3.00"),
            quantity=1,
            subtotal=Decimal("3.00"),
        )
    )
    await db_session.flush()

    updated = await apply_order_status(db_session, pure, "READY", source="STAFF", assert_kind="STAFF")
    assert updated.status == "READY"


@pytest.mark.asyncio
async def test_staff_direct_ready_rejected_for_mixed_order(db_session):
    store = Store(id=931, store_no="T-NC-04", name="测试店4", status=1)
    cat = ProductCategory(id=932, category_no="C932", store_id=store.id, name="卤味", sort_order=0, status=1)
    db_session.add_all([store, cat])
    await db_session.flush()

    cook = Product(
        id=933,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-NOODLE2",
        name="面",
        price=Decimal("18.00"),
        no_cooking=False,
    )
    ready = Product(
        id=934,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-EGG3",
        name="卤蛋",
        price=Decimal("3.00"),
        no_cooking=True,
    )
    db_session.add_all([cook, ready])
    await db_session.flush()

    mixed = Order(
        id=935,
        order_no="O-MIX3",
        store_id=store.id,
        status="CONFIRMED",
        subtotal=Decimal("21.00"),
        total_amount=Decimal("21.00"),
    )
    db_session.add(mixed)
    await db_session.flush()
    db_session.add_all(
        [
            OrderItem(
                id=936,
                order_id=mixed.id,
                product_id=cook.id,
                product_name_snapshot="面",
                unit_price_snapshot=Decimal("18.00"),
                quantity=1,
                subtotal=Decimal("18.00"),
            ),
            OrderItem(
                id=937,
                order_id=mixed.id,
                product_id=ready.id,
                product_name_snapshot="卤蛋",
                unit_price_snapshot=Decimal("3.00"),
                quantity=1,
                subtotal=Decimal("3.00"),
            ),
        ]
    )
    await db_session.flush()

    with pytest.raises(AppException) as exc:
        await apply_order_status(db_session, mixed, "READY", source="STAFF", assert_kind="STAFF")
    assert exc.value.code == 40027

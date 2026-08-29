"""PreferenceService 分母与写入门槛一致性。"""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    Customer,
    CustomerPreference,
    Order,
    OrderItem,
    OrderItemOption,
    Product,
    ProductCategory,
    Store,
)
from app.services.customer_service import PreferenceService


def test_preference_score_denominator_uses_payment_status():
    """分母按 payment_status 统计已支付订单，与 PAID 迁移写入偏好一致。"""
    assert PreferenceService.PREFERENCE_SCORE_PAYMENT_STATUSES == ("PAID", "PARTIAL_REFUND")


def test_kitchen_statuses_not_used_for_preference_denominator():
    """厨房态不应作为偏好分母（订单离开 PAID 后 status 会变，但 payment_status 不变）。"""
    kitchen = ("CONFIRMED", "PREPARING", "COOKING", "READY", "COMPLETED")
    for st in kitchen:
        assert st not in PreferenceService.PREFERENCE_SCORE_PAYMENT_STATUSES


@pytest.mark.asyncio
async def test_update_from_order_scores_by_payment_status(db_session):
    """分母统计 payment_status=PAID，不受厨房态 status 影响。"""
    store = Store(id=901, store_no="T-PREF-01", name="偏好测试店", status=1)
    customer = Customer(id=902, customer_no="C-PREF-01", nickname="偏好测试", status=1)
    cat = ProductCategory(id=903, store_id=store.id, name="面类", sort_order=1, status=1)
    db_session.add_all([store, customer, cat])
    await db_session.flush()

    product = Product(
        id=904,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-PREF-01",
        name="测试面",
        price=Decimal("18.00"),
        status="ON_SALE",
    )
    db_session.add(product)
    await db_session.flush()

    kitchen_order = Order(
        id=905,
        order_no="O-PREF-KITCHEN",
        customer_id=customer.id,
        store_id=store.id,
        status="COOKING",
        payment_status="PAID",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
    )
    paid_order = Order(
        id=906,
        order_no="O-PREF-PAID",
        customer_id=customer.id,
        store_id=store.id,
        status="PAID",
        payment_status="PAID",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
    )
    db_session.add_all([kitchen_order, paid_order])
    await db_session.flush()

    item = OrderItem(
        id=907,
        order_id=paid_order.id,
        product_id=product.id,
        product_name_snapshot="测试面",
        unit_price_snapshot=Decimal("18.00"),
        quantity=1,
        subtotal=Decimal("18.00"),
    )
    db_session.add(item)
    await db_session.flush()
    db_session.add(
        OrderItemOption(
            id=908,
            order_item_id=item.id,
            group_name_snapshot="辣度",
            option_name_snapshot="微辣",
            option_code="MILD",
        )
    )
    await db_session.commit()

    _next_pref_id = 920

    def _add_with_sqlite_ids(obj):
        nonlocal _next_pref_id
        if isinstance(obj, CustomerPreference) and getattr(obj, "id", None) is None:
            obj.id = _next_pref_id
            _next_pref_id += 1
        db_session.sync_session.add(obj)

    db_session.add = _add_with_sqlite_ids  # type: ignore[method-assign]

    await PreferenceService.update_from_order(db_session, paid_order.id)
    await db_session.commit()

    prefs = list(
        (
            await db_session.execute(
                select(CustomerPreference).where(CustomerPreference.customer_id == customer.id)
            )
        ).scalars()
    )
    assert len(prefs) == 1
    assert prefs[0].order_count == 1
    assert prefs[0].preference_score == Decimal("0.5000")


@pytest.mark.asyncio
async def test_update_from_order_skips_non_paid_status(db_session):
    """仅在 status=PAID 时写入偏好，COMPLETED 等状态不重复写入。"""
    store = Store(id=911, store_no="T-PREF-02", name="偏好测试店2", status=1)
    customer = Customer(id=912, customer_no="C-PREF-02", nickname="偏好测试2", status=1)
    db_session.add_all([store, customer])
    await db_session.flush()

    completed_order = Order(
        id=913,
        order_no="O-PREF-DONE",
        customer_id=customer.id,
        store_id=store.id,
        status="COMPLETED",
        payment_status="PAID",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
    )
    db_session.add(completed_order)
    await db_session.commit()

    await PreferenceService.update_from_order(db_session, completed_order.id)
    await db_session.commit()

    prefs = list(
        (
            await db_session.execute(
                select(CustomerPreference).where(CustomerPreference.customer_id == customer.id)
            )
        ).scalars()
    )
    assert prefs == []

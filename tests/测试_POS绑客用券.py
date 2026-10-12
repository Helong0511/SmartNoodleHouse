"""POS 绑客/重算须把优惠券计入应付（修复：仅会员折、清空券）。"""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.orders import _recompute_pos_amounts_for_customer
from app.core.response import money, naive_local_now
from app.models import (
    CouponTemplate,
    Customer,
    CustomerCoupon,
    MemberLevel,
    Order,
    OrderItem,
    Product,
    ProductCategory,
    Store,
)


async def _seed(db):
    store = Store(store_no="POSCP01", name="POS券测店", status=1)
    db.add(store)
    await db.flush()
    lv = MemberLevel(name="普通", min_points=0, discount_rate=Decimal("1.00"))
    db.add(lv)
    await db.flush()
    cust = Customer(
        customer_no="C-POS-CP",
        display_name="收银券客",
        member_status="MEMBER",
        account_status="ACTIVE",
        status=1,
        member_level_id=lv.id,
        registered_store_id=store.id,
    )
    db.add(cust)
    cat = ProductCategory(name="面", sort_order=1, status=1, store_id=store.id, category_no="CAT-POS-CP")
    db.add(cat)
    await db.flush()
    product = Product(
        name="肠旺面",
        product_no="P-POS-CP-1",
        price=Decimal("30.00"),
        status="ON_SALE",
        store_id=store.id,
        category_id=cat.id,
        stock=100,
    )
    db.add(product)
    tmpl = CouponTemplate(
        store_id=None,
        name="满30减5",
        coupon_type="FIXED",
        discount_value=Decimal("5.00"),
        min_order_amount=Decimal("30.00"),
        valid_days=7,
        status=1,
    )
    db.add(tmpl)
    await db.flush()
    cc = CustomerCoupon(
        customer_id=cust.id,
        template_id=tmpl.id,
        coupon_no="CP-POS-TEST-1",
        status="AVAILABLE",
        obtained_at=naive_local_now(),
        expires_at=naive_local_now() + timedelta(days=7),
    )
    db.add(cc)
    order = Order(
        order_no="OT-POS-CP-1",
        store_id=store.id,
        customer_id=None,
        channel="POS",
        dining_type="DINE_IN",
        status="PENDING_PAYMENT",
        payment_status="UNPAID",
        subtotal=Decimal("30.00"),
        discount_amount=Decimal("0.00"),
        coupon_discount=Decimal("0.00"),
        voucher_discount=Decimal("0.00"),
        packing_fee=Decimal("0.00"),
        total_amount=Decimal("30.00"),
        ordered_at=naive_local_now(),
    )
    db.add(order)
    await db.flush()
    db.add(
        OrderItem(
            order_id=order.id,
            product_id=product.id,
            product_name_snapshot=product.name,
            unit_price_snapshot=Decimal("30.00"),
            quantity=1,
            subtotal=Decimal("30.00"),
        )
    )
    await db.commit()
    return {"store": store, "cust": cust, "product": product, "order": order, "cc": cc, "tmpl": tmpl}


@pytest.mark.asyncio
async def test_pos_recompute_applies_coupon(db_session):
    seed = await _seed(db_session)
    order = await db_session.get(Order, seed["order"].id)
    assert order is not None
    order.customer_id = seed["cust"].id
    await _recompute_pos_amounts_for_customer(
        db_session,
        order,
        customer_coupon_id=seed["cc"].id,
    )
    await db_session.commit()

    order = await db_session.get(Order, seed["order"].id)
    assert order is not None
    assert money(order.coupon_discount) == money("5.00")
    assert int(order.customer_coupon_id) == int(seed["cc"].id)
    assert money(order.total_amount) == money("25.00")

    cc = await db_session.get(CustomerCoupon, seed["cc"].id)
    assert cc is not None
    assert cc.status == "RESERVED"
    assert int(cc.used_order_id) == int(order.id)


@pytest.mark.asyncio
async def test_pos_recompute_clears_coupon_when_unbind(db_session):
    seed = await _seed(db_session)
    order = await db_session.get(Order, seed["order"].id)
    assert order is not None
    order.customer_id = seed["cust"].id
    await _recompute_pos_amounts_for_customer(
        db_session,
        order,
        customer_coupon_id=seed["cc"].id,
    )
    await db_session.commit()

    order = await db_session.get(Order, seed["order"].id)
    assert order is not None
    order.customer_id = None
    await _recompute_pos_amounts_for_customer(db_session, order)
    await db_session.commit()

    order = await db_session.get(Order, seed["order"].id)
    assert order is not None
    assert order.customer_coupon_id is None
    assert money(order.coupon_discount) == money("0.00")
    assert money(order.total_amount) == money("30.00")

    cc = (
        await db_session.execute(select(CustomerCoupon).where(CustomerCoupon.id == seed["cc"].id))
    ).scalar_one()
    assert cc.status == "AVAILABLE"
    assert cc.used_order_id is None

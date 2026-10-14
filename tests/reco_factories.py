"""智能推荐系统测试共享工厂：构造门店/分类/餐品/顾客/订单等最小数据。

注意：Store/Product/Category 等表主键为纯 BIGINT，SQLite 下不会自动增长，
故所有工厂显式分配 id（每类独立序列，从 6000 起，避免与既有用例硬编码 id 冲突）。
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import (
    Customer,
    DiningTable,
    Option,
    OptionGroup,
    Order,
    OrderItem,
    OrderItemOption,
    Product,
    ProductCategory,
    ProductOptionGroup,
    Store,
)

# 每个测试函数独立建库（create_all/drop_all），id 仅需在单次用例内唯一
_STORE_ID = itertools.count(6000)
_CAT_ID = itertools.count(6000)
_PRODUCT_ID = itertools.count(6000)
_TABLE_ID = itertools.count(6000)
_GROUP_ID = itertools.count(6000)
_OPTION_ID = itertools.count(6000)
_ORDER_ID = itertools.count(6000)
_BIND_ID = itertools.count(6000)
_ITEM_ID = itertools.count(6000)
_ITEM_OPT_ID = itertools.count(6000)
_ORDER_NO = itertools.count(600000)
_SCHEDULE_ID = itertools.count(6000)
_SCHEDULE_ITEM_ID = itertools.count(6000)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def make_store(db, *, name: str = "测试面馆", store_no: str | None = None) -> Store:
    store = Store(id=next(_STORE_ID), store_no=store_no or f"S-{next(_ORDER_NO)}", name=name, status=1)
    db.add(store)
    await db.flush()
    return store


async def make_category(
    db, store_id: int, *, name: str = "招牌面", category_no: str | None = None, **extra
) -> ProductCategory:
    cat = ProductCategory(
        id=next(_CAT_ID),
        store_id=store_id,
        name=name,
        category_no=category_no or f"C-{next(_ORDER_NO)}",
        status=1,
        **extra,
    )
    db.add(cat)
    await db.flush()
    return cat


async def make_product(
    db,
    store_id: int,
    category_id: int,
    *,
    name: str = "红烧牛肉面",
    product_no: str | None = None,
    price: str = "28.00",
    status: str = "ON_SALE",
    sales_count: int = 0,
    is_hot: bool = False,
    is_recommended: bool = False,
    stock: int = 999,
) -> Product:
    p = Product(
        id=next(_PRODUCT_ID),
        store_id=store_id,
        category_id=category_id,
        product_no=product_no or f"P-{next(_ORDER_NO)}",
        name=name,
        price=Decimal(price),
        status=status,
        sales_count=sales_count,
        is_hot=is_hot,
        is_recommended=is_recommended,
        stock=stock,
    )
    db.add(p)
    await db.flush()
    return p


async def make_customer(db, *, customer_no: str | None = None, recommend_opt_in: int = 1) -> Customer:
    c = Customer(
        customer_no=customer_no or f"C{next(_ORDER_NO)}",
        recommend_opt_in=recommend_opt_in,
    )
    db.add(c)
    await db.flush()
    return c


async def make_table(db, store_id: int, *, qr_token: str | None = None) -> DiningTable:
    t = DiningTable(
        id=next(_TABLE_ID),
        store_id=store_id,
        table_no="T1",
        table_name="1号桌",
        qr_token=qr_token or f"qr-{next(_ORDER_NO)}",
        status=1,
    )
    db.add(t)
    await db.flush()
    return t


async def make_option_group(
    db, store_id: int | None, *, name: str = "辣度", code: str = "SPICY"
) -> OptionGroup:
    g = OptionGroup(id=next(_GROUP_ID), store_id=store_id, name=name, code=code, selection_type="SINGLE")
    db.add(g)
    await db.flush()
    return g


async def make_option(
    db, group_id: int, *, name: str = "微辣", code: str = "MILD", is_default: bool = False
) -> Option:
    o = Option(id=next(_OPTION_ID), group_id=group_id, name=name, code=code, is_default=is_default)
    db.add(o)
    await db.flush()
    return o


async def bind_option_group(db, product_id: int, group_id: int) -> None:
    db.add(ProductOptionGroup(id=next(_BIND_ID), product_id=product_id, group_id=group_id))
    await db.flush()


async def make_paid_order(
    db,
    customer_id: int,
    store_id: int,
    items: list[tuple[Product, int, list[tuple[str, str, str]]]],
    *,
    days_ago: float = 0,
    payment_status: str = "PAID",
    channel: str = "wechat",
) -> Order:
    """items: [(product, quantity, [(group_name, option_name, option_code), ...]), ...]"""
    when = utcnow() - timedelta(days=days_ago)
    total = sum((it[0].price or Decimal(0)) * it[1] for it in items)
    order = Order(
        id=next(_ORDER_ID),
        order_no=f"O{next(_ORDER_NO)}",
        customer_id=customer_id,
        store_id=store_id,
        status="PAID" if payment_status == "PAID" else "CONFIRMED",
        payment_status=payment_status,
        channel=channel,
        total_amount=total or Decimal("0.01"),
        subtotal=total or Decimal("0.01"),
        dining_type="DINE_IN",
        ordered_at=when,
        paid_at=when if payment_status in ("PAID", "PARTIAL_REFUND") else None,
    )
    db.add(order)
    await db.flush()
    for product, qty, opt_specs in items:
        oi = OrderItem(
            id=next(_ITEM_ID),
            order_id=order.id,
            product_id=product.id,
            product_name_snapshot=product.name,
            unit_price_snapshot=product.price,
            quantity=qty,
            subtotal=product.price * qty,
        )
        db.add(oi)
        await db.flush()
        for gname, oname, ocode in opt_specs:
            db.add(
                OrderItemOption(
                    id=next(_ITEM_OPT_ID),
                    order_item_id=oi.id,
                    group_name_snapshot=gname,
                    option_name_snapshot=oname,
                    option_code=ocode,
                    quantity=1,
                )
            )
    await db.flush()
    return order

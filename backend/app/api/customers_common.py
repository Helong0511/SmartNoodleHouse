"""顾客路由共享：门店可见范围与收藏列表序列化。"""
from __future__ import annotations

from sqlalchemy import or_, select

from app.core.exceptions import AppException
from app.core.response import money, serialize_dt
from app.models import Customer, CustomerFavoriteProduct, Order, Product


async def list_customer_favorites(db, customer_id: int, store_id: int | None = None) -> list[dict]:
    stmt = (
        select(CustomerFavoriteProduct, Product)
        .join(Product, Product.id == CustomerFavoriteProduct.product_id)
        .where(CustomerFavoriteProduct.customer_id == int(customer_id))
    )
    if store_id is not None:
        stmt = stmt.where(CustomerFavoriteProduct.store_id == int(store_id))
    stmt = stmt.order_by(
        CustomerFavoriteProduct.created_at.desc(), CustomerFavoriteProduct.id.desc()
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "product_id": int(product.id),
            "name": product.name,
            "price": str(money(product.price)),
            "image_url": product.main_image,
            "main_image": product.main_image,
            "store_id": int(product.store_id) if product.store_id else None,
            "status": product.status,
            "is_sold_out": int(product.stock or 0) <= 0,
            "favorited_at": serialize_dt(fav.created_at),
        }
        for fav, product in rows
    ]


def customer_in_store(store_id: int):
    """本店可见顾客：在本店下过单，或注册/首次到店记在本店。"""
    sid = int(store_id)
    return or_(
        Customer.registered_store_id == sid,
        Customer.first_seen_store_id == sid,
        Customer.id.in_(
            select(Order.customer_id).where(
                Order.store_id == sid,
                Order.customer_id.is_not(None),
            )
        ),
    )


async def assert_customer_in_store(
    db, customer_id: int, store_id: int, *, action: str = "查看"
) -> None:
    hit = await db.scalar(
        select(Customer.id)
        .where(Customer.id == int(customer_id), customer_in_store(store_id))
        .limit(1)
    )
    if not hit:
        raise AppException(40301, f"无权{action}其他门店顾客")

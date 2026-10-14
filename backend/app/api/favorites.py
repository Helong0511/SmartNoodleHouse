"""顾客菜品收藏：收藏/取消/列表（幂等）。"""
from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.deps import CurrentCustomer, DbSession
from app.core.exceptions import AppException
from app.core.response import money, success
from app.models import CustomerFavoriteProduct, Product

router = APIRouter(prefix="/miniprogram", tags=["收藏"])


@router.get("/favorites")
async def list_favorites(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    store_id: int | None = None,
):
    """我的收藏列表：按收藏时间倒序；餐品已删除（行不存在）的跳过。"""
    stmt = (
        select(CustomerFavoriteProduct, Product)
        .join(Product, Product.id == CustomerFavoriteProduct.product_id)
        .where(CustomerFavoriteProduct.customer_id == customer.id)
    )
    if store_id is not None:
        stmt = stmt.where(CustomerFavoriteProduct.store_id == int(store_id))
    stmt = stmt.order_by(
        CustomerFavoriteProduct.created_at.desc(), CustomerFavoriteProduct.id.desc()
    )
    rows = (await db.execute(stmt)).all()
    items = [
        {
            "product_id": int(product.id),
            "name": product.name,
            "price": str(money(product.price)),
            "image_url": product.main_image,
            "main_image": product.main_image,
            "store_id": int(product.store_id),
            "is_sold_out": int(product.stock or 0) <= 0,
        }
        for _, product in rows
    ]
    return success(items, request=request)


@router.post("/favorites/{product_id}")
async def add_favorite(product_id: int, request: Request, db: DbSession, customer: CurrentCustomer):
    """收藏菜品：餐品须存在且上架；已收藏直接返回（幂等）。"""
    product = await db.get(Product, product_id)
    if not product or product.status != "ON_SALE":
        raise AppException(40404, "餐品不存在或已下架")
    existing = (
        await db.execute(
            select(CustomerFavoriteProduct).where(
                CustomerFavoriteProduct.customer_id == customer.id,
                CustomerFavoriteProduct.product_id == product.id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            CustomerFavoriteProduct(
                customer_id=customer.id,
                product_id=product.id,
                store_id=product.store_id,
            )
        )
        await db.flush()
    return success({"favorited": True}, request=request)


@router.delete("/favorites/{product_id}")
async def remove_favorite(product_id: int, request: Request, db: DbSession, customer: CurrentCustomer):
    """取消收藏：不存在也返回成功（幂等）。"""
    fav = (
        await db.execute(
            select(CustomerFavoriteProduct).where(
                CustomerFavoriteProduct.customer_id == customer.id,
                CustomerFavoriteProduct.product_id == product_id,
            )
        )
    ).scalar_one_or_none()
    if fav is not None:
        await db.delete(fav)
        await db.flush()
    return success({"favorited": False}, request=request)

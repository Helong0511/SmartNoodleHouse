"""员工代操作顾客收藏：从 customers 路由拆出，路径仍挂在 /api/customers。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.customers_common import assert_customer_in_store, list_customer_favorites
from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import success
from app.models import Customer, CustomerFavoriteProduct, Product
from app.services.audit_service import AuditService
from app.services.store_scope import require_bound_store

router = APIRouter(prefix="/customers", tags=["顾客"])


@router.post("/{customer_id}/favorites/{product_id}", dependencies=[require_permission("customer:write")])
async def staff_add_favorite(
    customer_id: int,
    product_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """员工代为收藏菜品（幂等）。"""
    customer = await db.get(Customer, customer_id)
    if not customer:
        raise AppException(40401, "顾客不存在")
    effective_store = require_bound_store(user, None)
    if effective_store is not None:
        await assert_customer_in_store(db, customer_id, effective_store, action="编辑")

    product = await db.get(Product, product_id)
    if not product:
        raise AppException(40404, "餐品不存在")
    if effective_store is not None and int(product.store_id or 0) != int(effective_store):
        raise AppException(40301, "无权收藏其他门店餐品")

    existing = (
        await db.execute(
            select(CustomerFavoriteProduct).where(
                CustomerFavoriteProduct.customer_id == int(customer_id),
                CustomerFavoriteProduct.product_id == int(product.id),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            CustomerFavoriteProduct(
                customer_id=int(customer_id),
                product_id=int(product.id),
                store_id=int(product.store_id),
            )
        )
        await db.flush()
        await AuditService.log_staff(
            db,
            user=user,
            action="customer.favorite.add",
            module="customer",
            target_type="customer",
            target_id=int(customer_id),
            detail={"product_id": int(product.id), "store_id": int(product.store_id)},
            request=request,
            store_id=int(effective_store) if effective_store else int(product.store_id),
        )
    return success(
        {
            "favorited": True,
            "favorites": await list_customer_favorites(db, customer_id, store_id=effective_store),
        },
        request=request,
    )


@router.delete("/{customer_id}/favorites/{product_id}", dependencies=[require_permission("customer:write")])
async def staff_remove_favorite(
    customer_id: int,
    product_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """员工取消顾客收藏（幂等）。"""
    customer = await db.get(Customer, customer_id)
    if not customer:
        raise AppException(40401, "顾客不存在")
    effective_store = require_bound_store(user, None)
    if effective_store is not None:
        await assert_customer_in_store(db, customer_id, effective_store, action="编辑")

    fav = (
        await db.execute(
            select(CustomerFavoriteProduct).where(
                CustomerFavoriteProduct.customer_id == int(customer_id),
                CustomerFavoriteProduct.product_id == int(product_id),
            )
        )
    ).scalar_one_or_none()
    if fav is not None:
        await db.delete(fav)
        await db.flush()
        await AuditService.log_staff(
            db,
            user=user,
            action="customer.favorite.remove",
            module="customer",
            target_type="customer",
            target_id=int(customer_id),
            detail={"product_id": int(product_id)},
            request=request,
            store_id=int(effective_store) if effective_store else None,
        )
    return success(
        {
            "favorited": False,
            "favorites": await list_customer_favorites(db, customer_id, store_id=effective_store),
        },
        request=request,
    )

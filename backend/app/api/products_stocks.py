"""门店库存覆盖 CRUD：从 products 路由拆出，路径仍挂在 /api。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import calc_pages, success
from app.models import Order, OrderItem, Product, StoreProductStock
from app.services.audit_service import AuditService
from app.services.menu_cache_service import invalidate_store_menu_cache
from app.services.store_scope import assert_store_access, require_bound_store

router = APIRouter(tags=["餐品"])



async def _assert_no_active_orders_for_stock_mutation(
    db: DbSession, *, store_id: int, product_id: int
) -> None:
    """库存来源只能在没有未完成订单引用该餐品时切换。

    OrderItem 未记录扣减来源；在订单仍可取消/退款回库期间修改覆盖记录，
    会让 restore_stock 按新的覆盖状态把库存加回错误的库存池。
    """
    active_item_id = (
        await db.execute(
            select(OrderItem.id)
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.store_id == int(store_id),
                OrderItem.product_id == int(product_id),
                Order.status.notin_(("CANCELLED", "REFUNDED", "COMPLETED")),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active_item_id is not None:
        raise AppException(
            40962,
            "该餐品存在未完成订单，暂不能修改或删除门店库存覆盖；请待订单结束后再调整",
            status_code=409,
        )


class StoreStockUpsert(BaseModel):
    store_id: int | None = None
    stock: int = Field(default=999, ge=0, le=999999)
    status: int = Field(default=1, ge=0, le=1)


@router.get("/store-stocks", dependencies=[require_permission("product:read")])
async def list_store_stocks(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
    page: int = 1,
    page_size: int = 100,
    keyword: str | None = None,
):
    """分店库存覆盖列表（含品牌餐品名）。keyword 匹配餐品编号/名称。"""
    sid = require_bound_store(user, store_id)
    if sid is None:
        raise AppException(40060, "请指定门店")
    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 100)), 200)
    kw = (keyword or "").strip()
    pq = select(Product).where(Product.store_id == sid)
    if kw:
        like = f"%{kw}%"
        pq = pq.where(or_(Product.product_no.like(like), Product.name.like(like)))
    products = list((await db.execute(pq.order_by(Product.sort_order, Product.id))).scalars())
    overrides = {
        r.product_id: r
        for r in (
            await db.execute(select(StoreProductStock).where(StoreProductStock.store_id == sid))
        ).scalars()
    }
    items = []
    for p in products:
        ov = overrides.get(p.id)
        items.append(
            {
                "product_id": p.id,
                "product_no": p.product_no,
                "name": p.name,
                "brand_status": p.status,
                "brand_stock": p.stock,
                "store_id": sid,
                "stock": ov.stock if ov else p.stock,
                "status": ov.status if ov else 1,
                "has_override": ov is not None,
            }
        )
    total = len(items)
    start = (page - 1) * page_size
    slice_items = items[start : start + page_size]
    return success(
        {
            "store_id": sid,
            "items": slice_items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
        },
        request=request,
    )


@router.put("/store-stocks/{product_id}", dependencies=[require_permission("product:update")])
async def upsert_store_stock(
    product_id: int,
    data: StoreStockUpsert,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    sid = require_bound_store(user, data.store_id)
    if sid is None:
        raise AppException(40060, "请指定门店")
    assert_store_access(user, sid)
    # 与订单扣减/回库保持 Product -> StoreProductStock 的统一锁顺序。
    product = (
        await db.execute(
            select(Product).where(Product.id == int(product_id)).with_for_update()
        )
    ).scalar_one_or_none()
    if not product:
        raise AppException(40401, "餐品不存在")
    if int(product.store_id) != int(sid):
        raise AppException(40301, "餐品不属于该门店")
    await _assert_no_active_orders_for_stock_mutation(
        db, store_id=int(sid), product_id=int(product_id)
    )
    row = (
        await db.execute(
            select(StoreProductStock)
            .where(
                StoreProductStock.store_id == sid,
                StoreProductStock.product_id == product_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row:
        row.stock = int(data.stock)
        row.status = int(data.status)
    else:
        row = StoreProductStock(
            store_id=sid,
            product_id=product_id,
            stock=int(data.stock),
            status=int(data.status),
        )
        db.add(row)
    await db.flush()
    from app.services.stock_service import _emit_stock_mirror

    await _emit_stock_mirror(
        db,
        store_id=int(sid),
        product_id=int(product_id),
        mirror_id=int(row.id),
        zero=int(row.status) != 1 or int(row.stock) <= 0,
    )
    await invalidate_store_menu_cache(db, int(sid))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.stock.upsert",
        module="product",
        target_type="store_product_stock",
        target_id=int(product_id),
        detail={
            "store_id": int(sid),
            "product_id": int(product_id),
            "stock": int(row.stock),
            "status": int(row.status),
        },
        request=request,
        store_id=int(sid),
    )
    return success(
        {
            "store_id": sid,
            "product_id": product_id,
            "stock": row.stock,
            "status": row.status,
            "has_override": True,
        },
        request=request,
    )


@router.delete("/store-stocks/{product_id}", dependencies=[require_permission("product:update")])
async def delete_store_stock(
    product_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
):
    """删除分店覆盖，回退品牌库存。"""
    sid = require_bound_store(user, store_id)
    if sid is None:
        raise AppException(40060, "请指定门店")
    assert_store_access(user, sid)
    # 与订单扣减/回库保持 Product -> StoreProductStock 的统一锁顺序。
    product = (
        await db.execute(
            select(Product).where(Product.id == int(product_id)).with_for_update()
        )
    ).scalar_one_or_none()
    if product is None:
        raise AppException(40401, "餐品不存在")
    if int(product.store_id) != int(sid):
        raise AppException(40301, "餐品不属于该门店")
    await _assert_no_active_orders_for_stock_mutation(
        db, store_id=int(sid), product_id=int(product_id)
    )
    row = (
        await db.execute(
            select(StoreProductStock)
            .where(
                StoreProductStock.store_id == sid,
                StoreProductStock.product_id == product_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is not None:
        mirror_id = int(row.id)
        await db.delete(row)
        await db.flush()
        from app.services.stock_service import _emit_stock_mirror

        # 删除覆盖后 handler 会回退读取 products.stock 并同步该门店的有效库存。
        await _emit_stock_mirror(
            db,
            store_id=int(sid),
            product_id=int(product_id),
            mirror_id=mirror_id,
            zero=False,
        )
    await invalidate_store_menu_cache(db, int(sid))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.stock.delete",
        module="product",
        target_type="store_product_stock",
        target_id=int(product_id),
        detail={"store_id": int(sid), "product_id": int(product_id)},
        request=request,
        store_id=int(sid),
    )
    return success({"deleted": True, "store_id": sid, "product_id": product_id}, request=request)

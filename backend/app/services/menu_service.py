"""小程序菜单聚合服务（Task 12）：分类 + 在售商品一次成型。

口径与 GET /categories、GET /products 公开读路径保持一致：
- 分类 status=1、按当前时段方案重排；
- 商品 status=ON_SALE，NOT EXISTS 本店停售覆盖；售罄商品仍展示（不按库存过滤）；
- require_category_ids / require_product_ids 经继承链解析。
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.core.menu_cache import (
    bump_menu_version,
    get_menu_bundle,
    get_menu_version,
    set_menu_bundle,
)
from app.models import (
    Product,
    ProductCategory,
    ProductOptionGroup,
    Store,
    StoreProductStock,
)
from app.services.category_require import (
    normalize_require_category_ids,
    resolve_product_requires,
)
from app.services.category_schedule import apply_category_schedule_sort

_BUILD_TTL_SECONDS = 60
# 进程内兜底（Redis 不可用时仍避免每次请求重查）
_build_cache: dict[int, tuple[float, dict[str, Any]]] = {}


def _category_payload(cat: ProductCategory, store_name: str | None) -> dict[str, Any]:
    return {
        "id": cat.id,
        "store_id": cat.store_id,
        "store_name": store_name,
        "category_no": getattr(cat, "category_no", None) or "",
        "name": cat.name,
        "description": cat.description,
        "sort_order": cat.sort_order,
        "status": cat.status,
        "require_category_ids": normalize_require_category_ids(cat.require_category_ids),
    }


async def build_menu_bundle(db, store_id: int) -> dict[str, Any]:
    """直接查库组装菜单 bundle（不经过缓存）。"""
    sid = int(store_id)

    cat_rows = (
        await db.execute(
            select(ProductCategory)
            .where(ProductCategory.store_id == sid, ProductCategory.status == 1)
            .order_by(ProductCategory.sort_order, ProductCategory.id)
        )
    ).scalars().all()
    cats, active = await apply_category_schedule_sort(db, list(cat_rows), store_id=sid)

    stopped = aliased(StoreProductStock)
    stopped_exists = (
        select(1)
        .select_from(stopped)
        .where(
            stopped.product_id == Product.id,
            stopped.store_id == sid,
            stopped.status != 1,
        )
        .exists()
    )
    products = list(
        (
            await db.execute(
                select(Product, StoreProductStock.stock)
                .outerjoin(
                    StoreProductStock,
                    (StoreProductStock.product_id == Product.id)
                    & (StoreProductStock.store_id == sid),
                )
                .where(Product.store_id == sid, Product.status == "ON_SALE", ~stopped_exists)
                .order_by(Product.sort_order, Product.id)
            )
        ).all()
    )

    product_ids = [p.id for p, _ in products]
    has_options: set[int] = set()
    if product_ids:
        has_options = set(
            (
                await db.execute(
                    select(ProductOptionGroup.product_id).where(
                        ProductOptionGroup.product_id.in_(product_ids)
                    )
                )
            ).scalars().all()
        )

    cat_ids = list({p.category_id for p, _ in products if p.category_id})
    cats_map = {
        c.id: c
        for c in (
            await db.execute(select(ProductCategory).where(ProductCategory.id.in_(cat_ids)))
        ).scalars().all()
    }

    items: list[dict[str, Any]] = []
    for p, stock_override in products:
        cat = cats_map.get(p.category_id)
        eff_cats, eff_prods, inherited = resolve_product_requires(p, cat)
        items.append(
            {
                "id": p.id,
                "store_id": p.store_id,
                "category_id": p.category_id,
                "require_category_ids": eff_cats,
                "require_product_ids": eff_prods,
                "require_inherited": inherited,
                "product_no": p.product_no,
                "name": p.name,
                "description": p.description or "",
                "main_image": p.main_image,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                "price": str(p.price),
                "original_price": str(p.original_price) if p.original_price else None,
                "status": p.status,
                "is_recommended": p.is_recommended,
                "is_hot": p.is_hot,
                "no_member_discount": p.no_member_discount,
                "no_cooking": p.no_cooking,
                "sort_order": p.sort_order,
                "sales_count": p.sales_count,
                "stock": int(stock_override) if stock_override is not None else p.stock,
                "store_stock_override": stock_override is not None,
                "box_price": str(p.box_price or "0.00"),
                "has_options": p.id in has_options,
            }
        )

    store_name = None
    store = await db.get(Store, sid)
    if store:
        store_name = store.name

    categories = []
    for i, c in enumerate(cats):
        row = _category_payload(c, store_name)
        if active is not None:
            row["sort_order"] = i
            row["schedule_id"] = int(active.id)
            row["schedule_name"] = active.name
        categories.append(row)

    return {
        "categories": categories,
        "products": {"items": items, "total": len(items)},
    }


async def get_or_build_menu_bundle(db, store_id: int) -> dict[str, Any]:
    """带 Redis + 进程内两级缓存的菜单 bundle，并确保 version>=1。"""
    sid = int(store_id)
    cached = await get_menu_bundle(sid)
    if cached is not None:
        return cached

    hit = _build_cache.get(sid)
    if hit and hit[0] > time.time():
        bundle = hit[1]
    else:
        bundle = await build_menu_bundle(db, sid)
        _build_cache[sid] = (time.time() + _BUILD_TTL_SECONDS, bundle)

    version = await get_menu_version(sid)
    if version <= 0:
        # 首次上线/Redis 被清空：建立版本基线，使客户端 version 校验立即可用
        version = await bump_menu_version(sid)
    bundle = {**bundle, "version": version}
    await set_menu_bundle(sid, bundle)
    return bundle


def invalidate_local_menu_cache(store_id: int) -> None:
    _build_cache.pop(int(store_id), None)

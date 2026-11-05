"""小程序菜单聚合服务（Task 12）：分类 + 在售餐品一次成型。

口径与 GET /categories、GET /products 公开读路径保持一致：
- 分类 status=1、按当前时段方案重排；
- 餐品 status=ON_SALE，NOT EXISTS 本店停售覆盖；售罄餐品仍展示（不按库存过滤）；
- require_category_ids / require_product_ids 经继承链解析。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.core.hot_cache import HotCache
from app.core.menu_cache import (
    bump_menu_version,
    get_menu_bundle,
    get_menu_version,
    release_rebuild_lock,
    set_menu_bundle,
    try_acquire_rebuild_lock,
    wait_for_rebuild,
)
from app.core.response import now_local, serialize_dt
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
from app.services.category_schedule import (
    apply_category_schedule_sort,
    load_enabled_schedules,
    pick_active_schedule,
)
from app.utils.birthday import today_beijing
from app.utils.product_visibility import (
    filter_visible_product_dicts,
    visibility_payload,
)

_BUILD_TTL_SECONDS = 60
# 进程内兜底（Redis 不可用时仍避免每次请求重查）
_BUILD_CACHE: HotCache[int, dict[str, Any]] = HotCache(
    "menu.local_bundle", max_size=64, ttl_seconds=_BUILD_TTL_SECONDS
)


async def _active_schedule_id(db, store_id: int) -> int | None:
    """当前业务时刻命中的分类时段方案 id；无方案返回 None。"""
    schedules = await load_enabled_schedules(db, int(store_id))
    now_t = now_local().time().replace(tzinfo=None)
    active = pick_active_schedule(schedules, now_t)
    return int(active.id) if active else None


def _bundle_schedule_id(bundle: dict[str, Any] | None) -> int | None:
    if not bundle:
        return None
    raw = bundle.get("active_schedule_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


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
                "updated_at": serialize_dt(p.updated_at) if p.updated_at else None,
                "price": str(p.price),
                "original_price": str(p.original_price) if p.original_price else None,
                "status": p.status,
                "is_recommended": p.is_recommended,
                "is_hot": p.is_hot,
                "no_member_discount": p.no_member_discount,
                "no_cooking": p.no_cooking,
                **visibility_payload(p),
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
        "active_schedule_id": int(active.id) if active else None,
        "categories": categories,
        "products": {"items": items, "total": len(items)},
    }


def apply_menu_visibility(
    bundle: dict[str, Any],
    *,
    is_birthday_today: bool = False,
) -> dict[str, Any]:
    """按今日日历/生日规则过滤菜单餐品（不改缓存原文）。"""
    products = (bundle.get("products") or {}) if isinstance(bundle, dict) else {}
    raw_items = products.get("items") if isinstance(products, dict) else None
    if not isinstance(raw_items, list):
        return bundle
    today = today_beijing()
    filtered = filter_visible_product_dicts(
        raw_items, today=today, is_birthday_today=is_birthday_today
    )
    out = dict(bundle)
    out["products"] = {"items": filtered, "total": len(filtered)}
    return out


async def get_or_build_menu_bundle(db, store_id: int) -> dict[str, Any]:
    """带 Redis + 进程内两级缓存的菜单 bundle，并确保 version>=1。

    缓存命中时仍校验当前时段方案是否变化：跨午/晚市等窗口切换必须 bump 版本，
    否则小程序携带旧 version 会一直收到 changed:false，分类顺序不更新。
    """
    sid = int(store_id)
    expected_sched = await _active_schedule_id(db, sid)

    def _bundle_has_visibility_meta(bundle: dict[str, Any] | None) -> bool:
        """旧缓存若无 visibility_rule 字段，过滤会把限时菜当常显，须重建。"""
        if not bundle:
            return False
        products = bundle.get("products") or {}
        items = products.get("items") if isinstance(products, dict) else None
        if not isinstance(items, list) or not items:
            return True
        sample = items[0] if isinstance(items[0], dict) else {}
        return "visibility_rule" in sample

    cached = await get_menu_bundle(sid)
    if (
        cached is not None
        and _bundle_schedule_id(cached) == expected_sched
        and _bundle_has_visibility_meta(cached)
    ):
        return cached

    hit = _BUILD_CACHE.get(sid)
    if (
        hit is not None
        and _bundle_schedule_id(hit) == expected_sched
        and _bundle_has_visibility_meta(hit)
    ):
        bundle = hit
    else:
        # 互斥重建：避免缓存击穿时多进程同时 build_menu_bundle 压垮数据库
        lock_token = await try_acquire_rebuild_lock(sid)
        lock_ok = lock_token is not None
        if not lock_ok:
            # 未抢到锁：短暂等待其他进程重建完成后重读缓存
            waited = await wait_for_rebuild(sid, 0.3)
            if (
                waited is not None
                and _bundle_schedule_id(waited) == expected_sched
                and _bundle_has_visibility_meta(waited)
            ):
                version = await get_menu_version(sid)
                return {**waited, "version": version}
        try:
            # 时段方案切换或缓存过期：重建并抬升版本，通知客户端拉全量
            if cached is not None or (hit is not None and _bundle_schedule_id(hit) != expected_sched):
                invalidate_local_menu_cache(sid)
                await bump_menu_version(sid)
            bundle = await build_menu_bundle(db, sid)
            _BUILD_CACHE.put(sid, bundle)
        finally:
            if lock_ok:
                await release_rebuild_lock(sid, lock_token)

    version = await get_menu_version(sid)
    if version <= 0:
        # 首次上线/Redis 被清空：建立版本基线，使客户端 version 校验立即可用
        version = await bump_menu_version(sid)
    bundle = {**bundle, "version": version}
    await set_menu_bundle(sid, bundle)
    return bundle


def invalidate_local_menu_cache(store_id: int) -> None:
    _BUILD_CACHE.delete(int(store_id))

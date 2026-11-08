"""小程序菜单聚合服务（Task 12）：分类 + 在售餐品一次成型。

菜单缓存协调（Redis）不可用时，本服务对菜单读取 fail-closed：
不能验证远端 generation，就不能返回本地旧 bundle，也不能在未持有分布式
重建锁时自行回源。短暂 503 优先于跨 Worker 旧菜单或 MySQL 缓存击穿。

口径与 GET /categories、GET /products 公开读路径保持一致：
- 分类 status=1、按当前时段方案重排；
- 餐品 status=ON_SALE，NOT EXISTS 本店停售覆盖；售罄餐品仍展示（不按库存过滤）；
- require_category_ids / require_product_ids 经继承链解析。
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.core.hot_cache import HotCache
from app.core.exceptions import AppException
from app.core.menu_cache import (
    ensure_menu_generation,
    get_menu_bundle,
    get_menu_version,
    maintain_rebuild_lock,
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


def _bundle_version(bundle: dict[str, Any] | None) -> int:
    if not bundle:
        return 0
    try:
        return max(0, int(bundle.get("version") or 0))
    except (TypeError, ValueError):
        return 0


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

    # MySQL 是菜单代际与时段切换的持久事实源；Redis 丢失后可据此恢复。
    from app.services.menu_cache_service import sync_store_menu_schedule_generation

    durable_generation = await sync_store_menu_schedule_generation(db, sid, expected_sched)
    remote_version = await ensure_menu_generation(sid, durable_generation)
    if remote_version is None or remote_version != durable_generation:
        invalidate_local_menu_cache(sid)
        raise AppException(
            50301,
            "菜单版本协调服务暂不可用，请稍后重试",
            status_code=503,
        )
    cached = await get_menu_bundle(sid)
    if remote_version <= 0:
        # 仅保留首次初始化/版本键缺失的兼容路径；Redis 命令异常已由 None 区分。
        invalidate_local_menu_cache(sid)
    if (
        remote_version > 0
        and cached is not None
        and _bundle_schedule_id(cached) == expected_sched
        and _bundle_has_visibility_meta(cached)
        and _bundle_version(cached) == remote_version
    ):
        return cached

    lock_token: str | None = None
    lock_ok = False
    heartbeat_task: asyncio.Task | None = None
    build_version: int | None = None
    hit = _BUILD_CACHE.get(sid)
    # 本地缓存跨 worker 不会被直接删除；Redis 版本戳是远端失效广播。
    # 版本不一致时必须丢弃本进程旧 bundle，否则其它 worker 的更新会被旧值遮住 60 秒。
    if hit is not None and (
        remote_version <= 0 or _bundle_version(hit) != remote_version
    ):
        _BUILD_CACHE.delete(sid)
        hit = None
    if (
        remote_version > 0
        and hit is not None
        and _bundle_version(hit) == remote_version
        and _bundle_schedule_id(hit) == expected_sched
        and _bundle_has_visibility_meta(hit)
    ):
        bundle = hit
    else:
        # Redis 锁负责跨进程协调；本地 single-flight 负责 Redis 故障/等待者兜底，
        # 避免同一 worker 内所有请求在 300ms 等待后同时重建。
        from app.core.async_utils import local_singleflight

        async with local_singleflight(f"menu:{sid}"):
            # 等待本地前一个请求期间，缓存可能已经被填充；必须二次检查。
            cached_after_wait = await get_menu_bundle(sid)
            remote_after_wait = await get_menu_version(sid)
            if (
                remote_after_wait > 0
                and cached_after_wait is not None
                and _bundle_version(cached_after_wait) == remote_after_wait
                and _bundle_schedule_id(cached_after_wait) == expected_sched
                and _bundle_has_visibility_meta(cached_after_wait)
            ):
                bundle = cached_after_wait
            else:
                hit_after_wait = _BUILD_CACHE.get(sid)
                if (
                    hit_after_wait is not None
                    and remote_after_wait > 0
                    and _bundle_version(hit_after_wait) != remote_after_wait
                ):
                    _BUILD_CACHE.delete(sid)
                    hit_after_wait = None
                if (
                    remote_after_wait > 0
                    and hit_after_wait is not None
                    and _bundle_version(hit_after_wait) == remote_after_wait
                    and _bundle_schedule_id(hit_after_wait) == expected_sched
                    and _bundle_has_visibility_meta(hit_after_wait)
                ):
                    bundle = hit_after_wait
                else:
                    lock_token = await try_acquire_rebuild_lock(sid)
                    lock_ok = lock_token is not None
                    if lock_ok and lock_token:
                        owner_task = asyncio.current_task()
                        heartbeat_task = asyncio.create_task(
                            maintain_rebuild_lock(sid, lock_token, owner_task=owner_task)
                        )
                    if not lock_ok:
                        # 跨进程持锁者可能仍在构建；短暂等待后再查缓存。
                        waited = await wait_for_rebuild(sid, 2.0)
                        if (
                            remote_after_wait > 0
                            and waited is not None
                            and _bundle_version(waited) == remote_after_wait
                            and _bundle_schedule_id(waited) == expected_sched
                            and _bundle_has_visibility_meta(waited)
                        ):
                            bundle = waited
                        else:
                            # 未持有分布式锁时绝不能自行回源重建，否则每个 gunicorn
                            # worker 都会在 300ms 后同时打穿 MySQL。宁可短暂返回
                            # 503，也不在 Redis 故障/锁竞争时制造数据库雪崩。
                            raise AppException(
                                50301,
                                "菜单正在更新或缓存协调服务暂不可用，请稍后重试",
                                status_code=503,
                            )
                    else:
                        bundle = None
                    try:
                        if bundle is None:
                            # 版本只由 MySQL 的持久 generation 决定；缓存过期/重建本身
                            # 不得递增代际，否则 Redis 与数据库会出现不可恢复的分叉。
                            if cached is not None or (
                                hit is not None and _bundle_schedule_id(hit) != expected_sched
                            ):
                                invalidate_local_menu_cache(sid)
                            build_version = await get_menu_version(sid)
                            if build_version != durable_generation:
                                build_version = await ensure_menu_generation(sid, durable_generation)
                            if build_version is None or build_version <= 0:
                                raise AppException(
                                    50301,
                                    "菜单版本初始化失败，请稍后重试",
                                    status_code=503,
                                )
                            bundle = await build_menu_bundle(db, sid)
                            if _bundle_schedule_id(bundle) != expected_sched:
                                raise AppException(
                                    50301,
                                    "菜单时段刚刚发生变化，请重新获取",
                                    status_code=503,
                                )
                    except BaseException:
                        if heartbeat_task is not None:
                            heartbeat_task.cancel()
                            await asyncio.gather(heartbeat_task, return_exceptions=True)
                        if lock_ok:
                            await release_rebuild_lock(sid, owner_token=lock_token)
                        raise

    version = await get_menu_version(sid)
    if version <= 0:
        # Redis key 丢失时只能从 MySQL 持久代际恢复，不能自行 INCR 生成分叉版本。
        version = await ensure_menu_generation(sid, durable_generation) or 0
    if version <= 0:
        invalidate_local_menu_cache(sid)
        if lock_ok and lock_token:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            await release_rebuild_lock(sid, owner_token=lock_token)
            lock_ok = False
        raise AppException(
            50301,
            "菜单版本校验失败，请稍后重试",
            status_code=503,
        )
    if build_version is None and _bundle_version(bundle) != version:
        # 缓存命中/等待者路径也可能在读完 bundle 后发生并发失效；
        # 绝不能把旧 bundle 重新标记成新版本返回。
        latest = await get_menu_bundle(sid)
        latest_version = await get_menu_version(sid)
        if (
            latest is not None
            and latest_version > 0
            and _bundle_version(latest) == latest_version
            and _bundle_schedule_id(latest) == expected_sched
            and _bundle_has_visibility_meta(latest)
        ):
            bundle = latest
            version = latest_version
        else:
            invalidate_local_menu_cache(sid)
            raise AppException(
                50301,
                "菜单正在更新，请稍后重新获取",
                status_code=503,
            )
    # 若版本在构建期间发生变化，返回旧版本号，促使客户端下一次请求重新拉取；
    # 更重要的是绝不把旧快照伪装成最新版本发布到共享缓存。
    response_version = (
        build_version if build_version is not None and version != build_version else version
    )
    bundle = {**bundle, "version": response_version}
    if lock_ok and lock_token:
        try:
            published = await set_menu_bundle(
                sid,
                bundle,
                owner_token=lock_token,
                expected_version=build_version if build_version is not None else version,
            )
            if published:
                _BUILD_CACHE.put(sid, bundle)
            else:
                # 租约已丢失：不能把旧构建结果发布到 Redis 或本地共享缓存。
                invalidate_local_menu_cache(sid)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            await release_rebuild_lock(sid, owner_token=lock_token)
    elif cached is None and hit is None:
        # 仅命中进程内 single-flight/缓存的分支，不向 Redis 发布未持锁结果。
        pass
    return bundle


def invalidate_local_menu_cache(store_id: int) -> None:
    _BUILD_CACHE.delete(int(store_id))

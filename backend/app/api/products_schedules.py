"""分类时段排序方案 CRUD：从 products 路由拆出，路径仍挂在 /api。"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from sqlalchemy import delete, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import serialize_dt, success
from app.models import CategorySortSchedule, CategorySortScheduleItem, ProductCategory
from app.schemas.product import CategoryScheduleCreate, CategoryScheduleUpdate
from app.services.audit_service import AuditService
from app.services.category_schedule import parse_hhmm
from app.services.recommendation_service import invalidate_store_caches
from app.services.store_scope import assert_store_access, require_bound_store

router = APIRouter(tags=["餐品"])


def _fmt_time(t) -> str:
    if t is None:
        return ""
    return t.strftime("%H:%M:%S") if hasattr(t, "strftime") else str(t)


def schedule_payload(sched: CategorySortSchedule, items: list[CategorySortScheduleItem] | None = None) -> dict:
    item_rows = items or []
    auto_at = getattr(sched, "auto_updated_at", None)
    return {
        "id": sched.id,
        "store_id": sched.store_id,
        "name": sched.name,
        "start_time": _fmt_time(sched.start_time)[:5],
        "end_time": _fmt_time(sched.end_time)[:5],
        "priority": sched.priority,
        "status": sched.status,
        "auto_sort": int(getattr(sched, "auto_sort", 0) or 0),
        "auto_updated_at": serialize_dt(auto_at),
        "items": [
            {"category_id": r.category_id, "sort_order": r.sort_order}
            for r in sorted(item_rows, key=lambda x: (x.sort_order, x.category_id))
        ],
    }


async def load_schedule_items(db: DbSession, schedule_ids: list[int]) -> dict[int, list[CategorySortScheduleItem]]:
    if not schedule_ids:
        return {}
    rows = list(
        (
            await db.execute(
                select(CategorySortScheduleItem).where(
                    CategorySortScheduleItem.schedule_id.in_(schedule_ids)
                )
            )
        ).scalars()
    )
    out: dict[int, list[CategorySortScheduleItem]] = {i: [] for i in schedule_ids}
    for r in rows:
        out.setdefault(int(r.schedule_id), []).append(r)
    return out


async def replace_schedule_items(
    db: DbSession,
    schedule: CategorySortSchedule,
    items: list,
) -> None:
    await db.execute(
        delete(CategorySortScheduleItem).where(
            CategorySortScheduleItem.schedule_id == int(schedule.id)
        )
    )
    seen: set[int] = set()
    for raw in items or []:
        cid = int(raw.category_id if hasattr(raw, "category_id") else raw["category_id"])
        if cid in seen:
            continue
        seen.add(cid)
        cat = await db.get(ProductCategory, cid)
        if not cat or int(cat.store_id) != int(schedule.store_id):
            raise AppException(40001, f"分类不存在或不属于本店: {cid}")
        sort_order = int(raw.sort_order if hasattr(raw, "sort_order") else raw.get("sort_order", 0))
        db.add(
            CategorySortScheduleItem(
                schedule_id=int(schedule.id),
                category_id=cid,
                sort_order=sort_order,
            )
        )


@router.get("/category-schedules", dependencies=[require_permission("product:read")])
async def list_category_schedules(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
):
    sid = require_bound_store(user, store_id)
    if sid is None and not user.store_id and user.role.code != "SUPER_ADMIN":
        raise AppException(40020, "请指定门店")
    q = select(CategorySortSchedule).order_by(
        CategorySortSchedule.priority.desc(),
        CategorySortSchedule.start_time,
        CategorySortSchedule.id,
    )
    if sid is not None:
        assert_store_access(user, sid)
        q = q.where(CategorySortSchedule.store_id == int(sid))
    elif user.store_id:
        q = q.where(CategorySortSchedule.store_id == int(user.store_id))
    rows = list((await db.execute(q)).scalars().all())
    items_map = await load_schedule_items(db, [int(r.id) for r in rows])
    return success(
        [schedule_payload(r, items_map.get(int(r.id), [])) for r in rows],
        request=request,
    )


@router.post("/category-schedules", dependencies=[require_permission("product:create")])
async def create_category_schedule(
    data: CategoryScheduleCreate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    sid = require_bound_store(user, data.store_id)
    if sid is None:
        if user.store_id:
            sid = int(user.store_id)
        else:
            raise AppException(40020, "请指定门店后再创建时段方案")
    assert_store_access(user, sid)
    try:
        start_t = parse_hhmm(data.start_time)
        end_t = parse_hhmm(data.end_time)
    except ValueError as exc:
        raise AppException(40001, str(exc)) from exc
    sched = CategorySortSchedule(
        store_id=int(sid),
        name=data.name.strip(),
        start_time=start_t,
        end_time=end_t,
        priority=int(data.priority or 0),
        status=int(data.status if data.status is not None else 1),
        auto_sort=1 if int(data.auto_sort or 0) else 0,
    )
    if not sched.name:
        raise AppException(40001, "方案名称不能为空")
    db.add(sched)
    await db.flush()
    await replace_schedule_items(db, sched, data.items)
    await db.flush()
    items = (await load_schedule_items(db, [int(sched.id)])).get(int(sched.id), [])
    await invalidate_store_caches(int(sid))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.category_schedule.create",
        module="product",
        target_type="category_schedule",
        target_id=int(sched.id),
        detail={"store_id": int(sid), "name": sched.name},
        request=request,
        store_id=int(sid),
    )
    return success(schedule_payload(sched, items), request=request)


@router.put("/category-schedules/{schedule_id}", dependencies=[require_permission("product:update")])
async def update_category_schedule(
    schedule_id: int,
    data: CategoryScheduleUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    sched = await db.get(CategorySortSchedule, schedule_id)
    if not sched:
        raise AppException(40401, "时段方案不存在")
    assert_store_access(user, sched.store_id)
    payload = data.model_dump(exclude_unset=True)
    if "name" in payload and payload["name"] is not None:
        name = str(payload["name"]).strip()
        if not name:
            raise AppException(40001, "方案名称不能为空")
        sched.name = name
    if "start_time" in payload and payload["start_time"] is not None:
        try:
            sched.start_time = parse_hhmm(payload["start_time"])
        except ValueError as exc:
            raise AppException(40001, str(exc)) from exc
    if "end_time" in payload and payload["end_time"] is not None:
        try:
            sched.end_time = parse_hhmm(payload["end_time"])
        except ValueError as exc:
            raise AppException(40001, str(exc)) from exc
    if "priority" in payload and payload["priority"] is not None:
        sched.priority = int(payload["priority"])
    if "status" in payload and payload["status"] is not None:
        sched.status = int(payload["status"])
    if "auto_sort" in payload and payload["auto_sort"] is not None:
        sched.auto_sort = 1 if int(payload["auto_sort"]) else 0
    if "items" in payload and payload["items"] is not None:
        await replace_schedule_items(db, sched, data.items or [])
    await db.flush()
    items = (await load_schedule_items(db, [int(sched.id)])).get(int(sched.id), [])
    await invalidate_store_caches(int(sched.store_id))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.category_schedule.update",
        module="product",
        target_type="category_schedule",
        target_id=int(sched.id),
        detail={"store_id": int(sched.store_id) if sched.store_id else None, "fields": list(data.model_dump(exclude_unset=True).keys())},
        request=request,
        store_id=int(sched.store_id) if sched.store_id else None,
    )
    return success(schedule_payload(sched, items), request=request)


@router.post(
    "/category-schedules/{schedule_id}/auto-refresh",
    dependencies=[require_permission("product:update")],
)
async def auto_refresh_category_schedule(
    schedule_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """按该方案时段内历史销量立即刷新分类排序（不论是否开启 auto_sort）。"""
    from app.services.category_schedule_auto import (
        category_sort_auto_cfg,
        refresh_schedule_auto_sort,
    )

    sched = await db.get(CategorySortSchedule, schedule_id)
    if not sched:
        raise AppException(40401, "时段方案不存在")
    assert_store_access(user, sched.store_id)
    cfg = await category_sort_auto_cfg(db)
    result = await refresh_schedule_auto_sort(
        db,
        sched,
        lookback_days=cfg["lookback_days"],
        soft_qty=cfg["soft_qty"],
    )
    items = (await load_schedule_items(db, [int(sched.id)])).get(int(sched.id), [])
    await AuditService.log_staff(
        db,
        user=user,
        action="product.category_schedule.auto_refresh",
        module="product",
        target_type="category_schedule",
        target_id=int(sched.id),
        detail={
            "store_id": int(sched.store_id),
            "total_qty": result.get("total_qty"),
            "confidence": result.get("confidence"),
        },
        request=request,
        store_id=int(sched.store_id),
    )
    return success(
        {"result": result, "schedule": schedule_payload(sched, items)},
        request=request,
    )


@router.post(
    "/category-schedules/auto-refresh",
    dependencies=[require_permission("product:update")],
)
async def auto_refresh_store_category_schedules(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = Query(None),
    only_auto: bool = Query(True),
):
    """刷新门店下启用的时段方案；默认仅处理开启「销量自动」的方案。"""
    from app.services.category_schedule_auto import (
        category_sort_auto_cfg,
        refresh_store_category_schedules,
    )

    sid = require_bound_store(user, store_id)
    if sid is None:
        if user.store_id:
            sid = int(user.store_id)
        else:
            raise AppException(40020, "请指定门店后再刷新")
    assert_store_access(user, sid)
    cfg = await category_sort_auto_cfg(db)
    result = await refresh_store_category_schedules(
        db,
        int(sid),
        lookback_days=cfg["lookback_days"],
        soft_qty=cfg["soft_qty"],
        only_auto=bool(only_auto),
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="product.category_schedule.auto_refresh_store",
        module="product",
        target_type="store",
        target_id=int(sid),
        detail={"updated": result.get("updated"), "only_auto": bool(only_auto)},
        request=request,
        store_id=int(sid),
    )
    return success(result, request=request)


@router.delete("/category-schedules/{schedule_id}", dependencies=[require_permission("product:update")])
async def delete_category_schedule(
    schedule_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    sched = await db.get(CategorySortSchedule, schedule_id)
    if not sched:
        raise AppException(40401, "时段方案不存在")
    assert_store_access(user, sched.store_id)
    await db.execute(
        delete(CategorySortScheduleItem).where(
            CategorySortScheduleItem.schedule_id == int(sched.id)
        )
    )
    await db.delete(sched)
    await db.flush()
    await invalidate_store_caches(int(sched.store_id))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.category_schedule.delete",
        module="product",
        target_type="category_schedule",
        target_id=int(schedule_id),
        detail={"store_id": int(sched.store_id) if sched.store_id else None, "name": sched.name},
        request=request,
        store_id=int(sched.store_id) if sched.store_id else None,
    )
    return success({"id": schedule_id}, request=request)

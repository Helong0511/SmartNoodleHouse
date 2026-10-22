"""外卖产品/分类映射与同步告警：从 delivery_sync 拆出，路径仍挂在 /api/delivery。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import success, serialize_dt
from app.models import (
    DeliveryProductMap,
    DeliverySyncAlert,
    PlatformCategoryMap,
    PlatformStoreBinding,
    Product,
    ProductCategory,
)
from app.services.audit_service import AuditService
from app.services.product_sync_service import ProductSyncService

router = APIRouter(prefix="/delivery", tags=["外卖"])


class ProductMapCreate(BaseModel):
    store_id: int | None = None
    platform: str = Field(description="MEITUAN 或 ELEME")
    platform_sku_id: str
    platform_spu_code: str | None = None
    app_food_code: str | None = None
    option_signature: str | None = None
    platform_sku_name: str | None = None
    product_id: int
    option_map_json: str | None = None


@router.get("/product-maps", dependencies=[require_permission("product:read")])
async def list_product_maps(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    platform: str | None = None,
    store_id: int | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 100,
):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        return success(
            {"items": [], "total": 0, "page": 1, "page_size": page_size, "total_pages": 0},
            request=request,
        )
    assert_store_access(user, sid)
    store_id = int(sid)
    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 100)), 200)
    from sqlalchemy import func, or_

    from app.core.response import calc_pages

    q = select(DeliveryProductMap).where(DeliveryProductMap.store_id == store_id)
    if platform:
        q = q.where(DeliveryProductMap.platform == platform.upper())
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = q.where(
            or_(
                DeliveryProductMap.platform_sku_id.like(kw),
                DeliveryProductMap.platform_sku_name.like(kw),
                DeliveryProductMap.app_food_code.like(kw),
            )
        )
    total = int(await db.scalar(select(func.count()).select_from(q.subquery())) or 0)
    result = await db.execute(
        q.order_by(DeliveryProductMap.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    maps = list(result.scalars().all())
    product_ids = list({m.product_id for m in maps})
    products = {}
    if product_ids:
        products = {
            p.id: p
            for p in (await db.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()
        }
    items = [
        {
            "id": m.id,
            "platform": m.platform,
            "platform_sku_id": m.platform_sku_id,
            "platform_spu_code": m.platform_spu_code,
            "app_food_code": m.app_food_code,
            "option_signature": m.option_signature,
            "platform_sku_name": m.platform_sku_name,
            "product_id": m.product_id,
            "product_name": products[m.product_id].name if m.product_id in products else None,
            "status": m.status,
        }
        for m in maps
    ]
    return success(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
        },
        request=request,
    )


@router.post("/product-maps", dependencies=[require_permission("product:update")])
async def create_product_map(data: ProductMapCreate, request: Request, db: DbSession, user: CurrentUser):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, data.store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise AppException(40008, "缺少门店", status_code=400)
    assert_store_access(user, sid)
    product = await db.get(Product, data.product_id)
    if not product:
        return success(None, message="餐品不存在", request=request)
    if product.store_id is None or int(product.store_id) != int(sid):
        raise AppException(40301, "餐品不属于该门店，禁止映射", status_code=403)
    existing = await db.execute(
        select(DeliveryProductMap).where(
            DeliveryProductMap.store_id == sid,
            DeliveryProductMap.platform == data.platform.upper(),
            DeliveryProductMap.platform_sku_id == data.platform_sku_id,
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        row.product_id = data.product_id
        row.platform_sku_name = data.platform_sku_name
        row.platform_spu_code = data.platform_spu_code
        row.app_food_code = data.app_food_code or product.product_no
        row.option_signature = data.option_signature
        row.option_map_json = data.option_map_json
        row.status = 1
        await db.flush()
        await AuditService.log_staff(
            db,
            user=user,
            action="delivery.product_map.create",
            module="delivery",
            target_type="delivery_product_map",
            target_id=int(row.id),
            detail={"store_id": int(sid), "platform": data.platform.upper(), "platform_sku_id": data.platform_sku_id, "product_id": int(data.product_id), "updated": True},
            request=request,
            store_id=int(sid) if sid else None,
        )
        return success({"id": row.id, "updated": True}, request=request)

    row = DeliveryProductMap(
        store_id=sid,
        platform=data.platform.upper(),
        platform_sku_id=data.platform_sku_id,
        platform_spu_code=data.platform_spu_code,
        app_food_code=data.app_food_code or product.product_no,
        option_signature=data.option_signature,
        platform_sku_name=data.platform_sku_name,
        product_id=data.product_id,
        option_map_json=data.option_map_json,
    )
    db.add(row)
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.product_map.create",
        module="delivery",
        target_type="delivery_product_map",
        target_id=int(row.id),
        detail={"store_id": int(sid), "platform": data.platform.upper(), "platform_sku_id": data.platform_sku_id, "product_id": int(data.product_id), "updated": False},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success({"id": row.id, "updated": False}, request=request)


class CategoryMapPayload(BaseModel):
    platform: str
    local_category_id: int
    platform_category_code: str | None = None
    platform_category_name: str | None = None
    store_id: int | None = None


@router.get("/category-maps", dependencies=[require_permission("product:read")])
async def list_category_maps(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
    platform: str | None = None,
):
    """平台分类映射列表（含本地分类名）。"""
    from app.services.store_scope import require_bound_store

    sid = require_bound_store(user, store_id)
    rows = await ProductSyncService.list_category_maps(db, sid, platform)
    cat_ids = list({r.local_category_id for r in rows})
    cat_names = {
        cid: name
        for cid, name in (
            await db.execute(
                select(ProductCategory.id, ProductCategory.name).where(ProductCategory.id.in_(cat_ids or [-1]))
            )
        ).all()
    }
    items = [
        {
            "id": r.id,
            "store_id": r.store_id,
            "platform": r.platform,
            "local_category_id": r.local_category_id,
            "local_category_name": cat_names.get(r.local_category_id),
            "platform_category_code": r.platform_category_code,
            "platform_category_name": r.platform_category_name,
            "status": r.status,
        }
        for r in rows
    ]
    return success({"items": items, "total": len(items)}, request=request)


@router.post("/category-maps", dependencies=[require_permission("product:update")])
async def create_category_map(data: CategoryMapPayload, request: Request, db: DbSession, user: CurrentUser):
    """新增/更新平台分类映射（store_id 留空=总部默认）。"""
    from app.services.store_scope import assert_store_access

    # 非超管：必须绑定门店，store_id 留空强制写本店，禁止创建总部级映射
    if user.role.code != "SUPER_ADMIN":
        if not user.store_id:
            raise AppException(40301, "账号未绑定门店", status_code=403)
        data.store_id = int(user.store_id)
    if data.store_id:
        assert_store_access(user, data.store_id)
    if not await db.get(ProductCategory, data.local_category_id):
        raise AppException(40454, "本地分类不存在", status_code=400)
    row = await ProductSyncService.upsert_category_map(
        db,
        platform=data.platform,
        local_category_id=data.local_category_id,
        platform_category_code=data.platform_category_code,
        platform_category_name=data.platform_category_name,
        store_id=data.store_id,
    )
    warn = await ProductSyncService.hq_category_map_override_warning(
        db,
        store_id=data.store_id,
        platform=data.platform,
        local_category_id=int(data.local_category_id),
    )
    await db.commit()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.category_map.create",
        module="delivery",
        target_type="platform_category_map",
        target_id=int(row.id),
        detail={"store_id": int(data.store_id) if data.store_id else None, "platform": data.platform, "local_category_id": int(data.local_category_id)},
        request=request,
        store_id=int(data.store_id) if data.store_id else None,
    )
    body: dict = {"id": row.id}
    if warn:
        body["override_warning"] = warn
    return success(body, request=request)


@router.delete("/category-maps/{map_id}", dependencies=[require_permission("product:update")])
async def delete_category_map(map_id: int, request: Request, db: DbSession, user: CurrentUser):
    from app.services.store_scope import assert_store_access

    row = await db.get(PlatformCategoryMap, map_id)
    if not row:
        raise AppException(40451, "映射不存在", status_code=404)
    # store_id 为 NULL 的总部级映射仅超管可删除；assert_store_access 对非超管的 None 一律拒绝
    assert_store_access(user, row.store_id)
    ok = await ProductSyncService.delete_category_map(db, map_id)
    await db.commit()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.category_map.delete",
        module="delivery",
        target_type="platform_category_map",
        target_id=int(map_id),
        detail={"store_id": int(row.store_id) if row.store_id else None, "platform": row.platform, "deleted": bool(ok)},
        request=request,
        store_id=int(row.store_id) if row.store_id else None,
    )
    return success({"id": map_id, "deleted": ok}, request=request)


@router.get("/sync-alerts", dependencies=[require_permission("product:read")])
async def list_sync_alerts(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    platform: str | None = None,
    alert_type: str | None = None,
    resolved: int | None = None,
    page: int = 1,
    page_size: int = 50,
):
    """外卖同步/拒单告警列表。"""
    from sqlalchemy import func

    from app.core.response import calc_pages

    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 50)), 200)
    q = select(DeliverySyncAlert)
    if user.role.code != "SUPER_ADMIN":
        if not user.store_id:
            raise AppException(40301, "账号未绑定门店", status_code=403)
        q = q.where(DeliverySyncAlert.store_id == int(user.store_id))
    elif getattr(user, "store_id", None):
        q = q.where(DeliverySyncAlert.store_id == int(user.store_id))
    if platform:
        q = q.where(DeliverySyncAlert.platform == platform.upper())
    if alert_type:
        q = q.where(DeliverySyncAlert.alert_type == alert_type.strip().upper())
    if resolved is not None:
        q = q.where(DeliverySyncAlert.resolved == int(resolved))
    total = int(await db.scalar(select(func.count()).select_from(q.subquery())) or 0)
    rows = list(
        (
            await db.execute(
                q.order_by(DeliverySyncAlert.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": r.id,
            "store_id": r.store_id,
            "platform": r.platform,
            "alert_type": r.alert_type,
            "title": r.title,
            "detail": r.detail,
            "platform_order_id": r.platform_order_id,
            "resolved": r.resolved,
            "created_at": serialize_dt(r.created_at),
            "resolved_at": serialize_dt(r.resolved_at),
        }
        for r in rows
    ]
    return success(
        {"items": items, "total": total, "page": page, "page_size": page_size,
         "total_pages": calc_pages(total, page_size)},
        request=request,
    )


@router.post("/sync-alerts/{alert_id}/resolve", dependencies=[require_permission("product:update")])
async def resolve_sync_alert(alert_id: int, request: Request, db: DbSession, user: CurrentUser):
    from app.core.response import naive_local_now

    row = await db.get(DeliverySyncAlert, alert_id)
    if not row:
        raise AppException(40452, "告警不存在", status_code=404)
    # store_id 为 NULL 的平台级告警仅超管可处理；assert_store_access 对非超管的 None 一律拒绝
    from app.services.store_scope import assert_store_access

    assert_store_access(user, row.store_id)
    row.resolved = 1
    row.resolved_at = naive_local_now()
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync_alert.resolve",
        module="delivery",
        target_type="delivery_sync_alert",
        target_id=int(row.id),
        detail={"store_id": int(row.store_id) if row.store_id else None, "alert_type": row.alert_type, "title": row.title},
        request=request,
        store_id=int(row.store_id) if row.store_id else None,
    )
    return success({"id": row.id, "resolved": 1}, request=request)


@router.get("/sync/overview", dependencies=[require_permission("product:read")])
async def sync_overview(request: Request, db: DbSession, user: CurrentUser):
    """同步监控总览：各平台绑定状态 + 未解决告警数。"""
    from sqlalchemy import func

    if user.role.code != "SUPER_ADMIN" and not user.store_id:
        raise AppException(40301, "账号未绑定门店", status_code=403)
    q = select(PlatformStoreBinding)
    if getattr(user, "store_id", None):
        q = q.where(PlatformStoreBinding.store_id == int(user.store_id))
    bindings = list((await db.execute(q.order_by(PlatformStoreBinding.id.desc()))).scalars().all())

    unresolved_q = select(func.count()).select_from(DeliverySyncAlert).where(DeliverySyncAlert.resolved == 0)
    if getattr(user, "store_id", None):
        unresolved_q = unresolved_q.where(DeliverySyncAlert.store_id == int(user.store_id))
    unresolved = int(await db.scalar(unresolved_q) or 0)

    items = [
        {
            "binding_id": b.id,
            "store_id": b.store_id,
            "platform": b.platform,
            "platform_shop_id": b.platform_shop_id,
            "platform_shop_name": b.platform_shop_name,
            "status": b.status,
            "has_token": bool(b.access_token),
            "token_expires_at": serialize_dt(b.token_expires_at),
            "auto_sync_open": bool(b.auto_sync_open),
            "platform_open_status": b.platform_open_status,
            "last_sync_at": serialize_dt(b.last_sync_at),
        }
        for b in bindings
    ]
    return success({"bindings": items, "unresolved_alerts": unresolved}, request=request)

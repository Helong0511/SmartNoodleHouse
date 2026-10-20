"""外卖菜单同步推送：映射/告警见 delivery_maps，绑定见 delivery_bindings。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import success
from app.services.audit_service import AuditService
from app.services.product_sync_service import ProductSyncService

router = APIRouter(prefix="/delivery", tags=["外卖"])

from app.api.delivery_bindings import change_store_open_status  # noqa: F401  # 兼容 delivery 旧导入


class SingleProductSyncPayload(BaseModel):
    store_id: int | None = None
    platform: str
    product_id: int


@router.post("/sync/menu", dependencies=[require_permission("product:update")])
async def sync_menu(
    request: Request, db: DbSession, user: CurrentUser, store_id: int | None = None, platform: str = "MEITUAN"
):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise AppException(40008, "缺少门店", status_code=400)
    assert_store_access(user, sid)
    result = await ProductSyncService.sync_store_menu(db, int(sid), platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.menu",
        module="delivery",
        target_type="store",
        target_id=int(sid),
        detail={"store_id": int(sid), "platform": platform, **result},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success(result, request=request)


@router.post("/sync/product", dependencies=[require_permission("product:update")])
async def sync_single_product(
    data: SingleProductSyncPayload, request: Request, db: DbSession, user: CurrentUser
):
    """推送单个餐品到指定外卖平台。"""
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, data.store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise AppException(40008, "缺少门店", status_code=400)
    assert_store_access(user, sid)
    result = await ProductSyncService.sync_single_product(db, int(sid), data.platform, data.product_id)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.product",
        module="delivery",
        target_type="product",
        target_id=int(data.product_id),
        detail={"store_id": int(sid), "platform": data.platform, "product_id": int(data.product_id), **result},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success(result, request=request)


@router.post("/sync/prices", dependencies=[require_permission("product:update")])
async def sync_prices(
    request: Request, db: DbSession, user: CurrentUser, store_id: int | None = None, platform: str = "MEITUAN"
):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise AppException(40008, "缺少门店", status_code=400)
    assert_store_access(user, sid)
    result = await ProductSyncService.sync_prices(db, int(sid), platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.prices",
        module="delivery",
        target_type="store",
        target_id=int(sid),
        detail={"store_id": int(sid), "platform": platform, **result},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success(result, request=request)


@router.post("/sync/stock", dependencies=[require_permission("product:update")])
async def sync_stock(
    request: Request, db: DbSession, user: CurrentUser, store_id: int | None = None, platform: str = "MEITUAN"
):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise AppException(40008, "缺少门店", status_code=400)
    assert_store_access(user, sid)
    result = await ProductSyncService.sync_stock(db, int(sid), platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.stock",
        module="delivery",
        target_type="store",
        target_id=int(sid),
        detail={"store_id": int(sid), "platform": platform, **result},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success(result, request=request)


# ═════════════════════ 外卖管理重构：营业 / 菜单 / 监控 ═════════════════════

class ShelfPayload(BaseModel):
    store_id: int
    platform: str
    on_shelf: bool
    product_ids: list[int] | None = None


class ReconcilePayload(BaseModel):
    store_id: int | None = None
    platform: str | None = None


@router.post("/sync/menu-batch", dependencies=[require_permission("product:update")])
async def sync_menu_batch(
    request: Request, db: DbSession, user: CurrentUser, store_id: int, platform: str = "MEITUAN"
):
    """批量推送门店菜单到平台（建档/更新），语义同 /sync/menu。"""
    from app.services.store_scope import assert_store_access

    assert_store_access(user, store_id)
    result = await ProductSyncService.sync_store_menu(db, store_id, platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.menu_batch",
        module="delivery",
        target_type="store",
        target_id=int(store_id),
        detail={"store_id": int(store_id), "platform": platform, **result},
        request=request,
        store_id=int(store_id),
    )
    return success(result, request=request)


@router.post("/sync/shelf", dependencies=[require_permission("product:update")])
async def sync_shelf(data: ShelfPayload, request: Request, db: DbSession, user: CurrentUser):
    """外卖平台侧批量上架/下架（不影响本地堂食）。"""
    from app.services.store_scope import assert_store_access

    assert_store_access(user, data.store_id)
    result = await ProductSyncService.set_shelf(
        db, data.store_id, data.platform, data.on_shelf, data.product_ids
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.shelf",
        module="delivery",
        target_type="store",
        target_id=int(data.store_id),
        detail={"store_id": int(data.store_id), "platform": data.platform, "on_shelf": bool(data.on_shelf), "product_ids": list(data.product_ids or []), **result},
        request=request,
        store_id=int(data.store_id),
    )
    return success(result, request=request)


@router.post("/sync/reconcile", dependencies=[require_permission("product:update")])
async def sync_reconcile(data: ReconcilePayload, request: Request, db: DbSession, user: CurrentUser):
    """手动触发库存对账：本地库存覆盖推送平台。"""
    from app.services.store_scope import assert_store_access, require_bound_store

    if user.role.code == "SUPER_ADMIN":
        sid = int(data.store_id) if data.store_id else None
        if sid is None:
            raise AppException(40061, "请指定要对账的门店")
        assert_store_access(user, sid)
    else:
        sid = require_bound_store(user, data.store_id)
        if sid is None:
            raise AppException(40301, "账号未绑定门店")
    result = await ProductSyncService.reconcile_stock(db, sid, data.platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.reconcile",
        module="delivery",
        target_type="store",
        target_id=int(sid) if sid else None,
        detail={"store_id": int(sid) if sid else None, "platform": data.platform, **result},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success(result, request=request)

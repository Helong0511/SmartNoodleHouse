"""外卖平台门店绑定与营业开关：从 delivery_sync 拆出，路径仍挂在 /api/delivery。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import success, serialize_dt
from app.models import PlatformStoreBinding
from app.services.audit_service import AuditService

router = APIRouter(prefix="/delivery", tags=["外卖"])


class StoreBindingCreate(BaseModel):
    store_id: int | None = None
    platform: str
    platform_shop_id: str
    platform_shop_name: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None


@router.get("/store-bindings", dependencies=[require_permission("product:read")])
async def list_store_bindings(request: Request, db: DbSession, user: CurrentUser, store_id: int | None = None):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        return success({"items": []}, request=request)
    assert_store_access(user, sid)
    result = await db.execute(
        select(PlatformStoreBinding).where(PlatformStoreBinding.store_id == sid).order_by(PlatformStoreBinding.platform)
    )
    items = [
        {
            "id": b.id,
            "store_id": b.store_id,
            "platform": b.platform,
            "platform_shop_id": b.platform_shop_id,
            "platform_shop_name": b.platform_shop_name,
            "status": b.status,
            "has_token": bool(b.access_token),
            "has_refresh_token": bool(b.refresh_token),
            "token_expires_at": serialize_dt(b.token_expires_at),
            "auto_sync_open": bool(b.auto_sync_open),
            "platform_open_status": b.platform_open_status,
        }
        for b in result.scalars()
    ]
    return success({"items": items}, request=request)


@router.post("/store-bindings", dependencies=[require_permission("product:update")])
async def upsert_store_binding(data: StoreBindingCreate, request: Request, db: DbSession, user: CurrentUser):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, data.store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise AppException(40008, "缺少门店", status_code=400)
    assert_store_access(user, sid)
    existing = (
        await db.execute(
            select(PlatformStoreBinding).where(
                PlatformStoreBinding.store_id == sid,
                PlatformStoreBinding.platform == data.platform.upper(),
                PlatformStoreBinding.platform_shop_id == data.platform_shop_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.platform_shop_name = data.platform_shop_name
        if data.access_token:
            from app.services.secret_encryption import SecretEncryptionService

            existing.access_token = SecretEncryptionService.encrypt(data.access_token)
        if data.refresh_token:
            from app.services.secret_encryption import SecretEncryptionService

            existing.refresh_token = SecretEncryptionService.encrypt(data.refresh_token)
        existing.status = 1
        await db.flush()
        await AuditService.log_staff(
            db,
            user=user,
            action="delivery.binding.create",
            module="delivery",
            target_type="platform_store_binding",
            target_id=int(existing.id),
            detail={"store_id": int(sid), "platform": data.platform.upper(), "platform_shop_id": data.platform_shop_id, "updated": True},
            request=request,
            store_id=int(sid) if sid else None,
        )
        return success({"id": existing.id, "updated": True}, request=request)

    from app.services.secret_encryption import SecretEncryptionService

    row = PlatformStoreBinding(
        store_id=sid,
        platform=data.platform.upper(),
        platform_shop_id=data.platform_shop_id,
        platform_shop_name=data.platform_shop_name,
        access_token=SecretEncryptionService.encrypt(data.access_token) if data.access_token else None,
        refresh_token=SecretEncryptionService.encrypt(data.refresh_token) if data.refresh_token else None,
    )
    db.add(row)
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.binding.create",
        module="delivery",
        target_type="platform_store_binding",
        target_id=int(row.id),
        detail={"store_id": int(sid), "platform": data.platform.upper(), "platform_shop_id": data.platform_shop_id, "updated": False},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success({"id": row.id, "updated": False}, request=request)


@router.get("/store-bindings/{binding_id}", dependencies=[require_permission("product:read")])
async def get_store_binding(binding_id: int, request: Request, db: DbSession, user: CurrentUser):
    """获取绑定详情（含 token 过期时间，便于管理后台展示）。"""
    from app.services.store_scope import assert_store_access

    row = await db.get(PlatformStoreBinding, binding_id)
    if not row:
        raise AppException(40450, "绑定不存在", status_code=404)
    assert_store_access(user, row.store_id)
    return success(
        {
            "id": row.id,
            "store_id": row.store_id,
            "platform": row.platform,
            "platform_shop_id": row.platform_shop_id,
            "platform_shop_name": row.platform_shop_name,
            "has_token": bool(row.access_token),
            "has_refresh_token": bool(row.refresh_token),
            "token_expires_at": serialize_dt(row.token_expires_at),
            "status": row.status,
            "updated_at": serialize_dt(row.updated_at),
        },
        request=request,
    )


@router.delete("/store-bindings/{binding_id}", dependencies=[require_permission("product:update")])
async def delete_store_binding(binding_id: int, request: Request, db: DbSession, user: CurrentUser):
    """解绑平台门店：清除 token 并停用，不真正删除行（保留审计/对账链路）。"""
    from app.services.store_scope import assert_store_access

    row = await db.get(PlatformStoreBinding, binding_id)
    if not row:
        raise AppException(40450, "绑定不存在", status_code=404)
    assert_store_access(user, row.store_id)
    row.access_token = None
    row.refresh_token = None
    row.token_expires_at = None
    row.status = 0
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.binding.delete",
        module="delivery",
        target_type="platform_store_binding",
        target_id=int(row.id),
        detail={"store_id": int(row.store_id) if row.store_id else None, "platform": row.platform, "platform_shop_id": row.platform_shop_id},
        request=request,
        store_id=int(row.store_id) if row.store_id else None,
    )
    return success({"id": row.id, "status": 0}, request=request)


class BindingStatusUpdate(BaseModel):
    status: int = Field(ge=0, le=1, description="1=启用, 0=停用")


@router.patch("/store-bindings/{binding_id}/status", dependencies=[require_permission("product:update")])
async def update_store_binding_status(
    binding_id: int, data: BindingStatusUpdate, request: Request, db: DbSession, user: CurrentUser
):
    """切换绑定启用/停用（停用后回调会被拒绝落单）。"""
    from app.services.store_scope import assert_store_access

    row = await db.get(PlatformStoreBinding, binding_id)
    if not row:
        raise AppException(40450, "绑定不存在", status_code=404)
    assert_store_access(user, row.store_id)
    row.status = int(data.status)
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.binding.status",
        module="delivery",
        target_type="platform_store_binding",
        target_id=int(row.id),
        detail={"store_id": int(row.store_id) if row.store_id else None, "status": int(row.status)},
        request=request,
        store_id=int(row.store_id) if row.store_id else None,
    )
    return success({"id": row.id, "status": row.status}, request=request)


@router.post("/store-bindings/{binding_id}/refresh-token", dependencies=[require_permission("product:update")])
async def refresh_store_binding_token(
    binding_id: int, request: Request, db: DbSession, user: CurrentUser
):
    """手动触发 Token 刷新（美团/饿了么均支持）。失败返回原因。"""
    from app.services.platform_token_service import PlatformTokenService
    from app.services.store_scope import assert_store_access

    row = await db.get(PlatformStoreBinding, binding_id)
    if not row:
        raise AppException(40450, "绑定不存在", status_code=404)
    assert_store_access(user, row.store_id)
    if not row.refresh_token:
        raise AppException(40051, "未保存 refresh_token，无法刷新", status_code=400)
    ok = await PlatformTokenService.refresh_single_binding(row)
    await db.commit()
    if not ok:
        raise AppException(50040, "刷新失败，请检查日志或重试", status_code=500)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.binding.refresh_token",
        module="delivery",
        target_type="platform_store_binding",
        target_id=int(row.id),
        detail={"store_id": int(row.store_id) if row.store_id else None, "platform": row.platform, "token_expires_at": serialize_dt(row.token_expires_at)},
        request=request,
        store_id=int(row.store_id) if row.store_id else None,
    )
    return success(
        {
            "id": row.id,
            "token_expires_at": serialize_dt(row.token_expires_at),
        },
        request=request,
    )


class HoursSlot(BaseModel):
    open_time: str = Field(description="HH:MM")
    close_time: str = Field(description="HH:MM")
    cross_day: bool = False


class HoursDay(BaseModel):
    weekday: int = Field(ge=1, le=7, description="1=周一 ... 7=周日")
    is_open: bool = True
    slots: list[HoursSlot] = []


class HoursPayload(BaseModel):
    days: list[HoursDay]


class AutoSyncOpenPayload(BaseModel):
    auto_sync_open: bool


class StoreOpenPayload(BaseModel):
    platform: str | None = None


@router.get("/stores/{store_id}/business-hours", dependencies=[require_permission("product:read")])
async def get_business_hours(store_id: int, request: Request, db: DbSession, user: CurrentUser):
    """读取门店一周营业时间。"""
    from app.services.store_hours_service import StoreHoursService
    from app.services.store_scope import assert_store_access

    assert_store_access(user, store_id)
    days = await StoreHoursService.get_hours_payload(db, store_id)
    return success({"store_id": store_id, "days": days}, request=request)


@router.put("/stores/{store_id}/business-hours", dependencies=[require_permission("product:update")])
async def set_business_hours(store_id: int, data: HoursPayload, request: Request, db: DbSession, user: CurrentUser):
    """全量覆盖门店一周营业时间（is_open=false 或无 slots = 当天休息）。"""
    from app.services.store_hours_service import StoreHoursService
    from app.services.store_scope import assert_store_access

    assert_store_access(user, store_id)
    try:
        days = await StoreHoursService.set_hours(db, store_id, [d.model_dump() for d in data.days])
    except ValueError as e:
        raise AppException(40000, str(e), status_code=400)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.business_hours.update",
        module="delivery",
        target_type="store",
        target_id=int(store_id),
        detail={"store_id": int(store_id), "days_count": len(data.days)},
        request=request,
        store_id=int(store_id),
    )
    return success({"store_id": store_id, "days": days}, request=request)


async def change_store_open_status(db, user, store_id: int, open_status: str, platform: str | None):
    """手动开/关店：先组任务并 commit，再调平台，最后短事务写回（与自动开关店一致）。"""
    from app.core.response import naive_local_now
    from app.services.delivery_context import get_credentials_for_store
    from app.services.delivery_providers import get_delivery_provider
    from app.services.store_hours_service import StoreHoursService, _python_weekday, shipping_time_for_day
    from app.services.store_scope import assert_store_access

    assert_store_access(user, store_id)
    stmt = select(PlatformStoreBinding).where(
        PlatformStoreBinding.store_id == store_id,
        PlatformStoreBinding.status == 1,
    )
    if platform:
        stmt = stmt.where(PlatformStoreBinding.platform == platform.upper())
    bindings = list((await db.execute(stmt)).scalars().all())
    if not bindings:
        raise AppException(40050, "该门店没有已启用的平台绑定", status_code=400)

    now = naive_local_now()
    rows = await StoreHoursService.list_hours(db, store_id)
    shipping = shipping_time_for_day(rows, _python_weekday(now)) if open_status == "REST" else None

    jobs = []
    for b in bindings:
        creds = await get_credentials_for_store(db, b.platform, store_id)
        jobs.append((int(b.id), b.platform, creds))
    await db.commit()

    results = []
    for binding_id, plat, creds in jobs:
        try:
            provider = get_delivery_provider(plat, creds)
            ok = await provider.set_store_open_status(open_status, shipping)
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            row = await db.get(PlatformStoreBinding, binding_id)
            if row:
                row.platform_open_status = open_status
                row.last_sync_at = now
                await db.flush()
        results.append({"platform": plat, "ok": ok})
    await db.commit()
    return results


@router.post("/stores/{store_id}/open", dependencies=[require_permission("product:update")])
async def open_delivery_store(store_id: int, data: StoreOpenPayload, request: Request, db: DbSession, user: CurrentUser):
    """手动开店（平台上线营业）。"""
    results = await change_store_open_status(db, user, store_id, "OPEN", data.platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.store.open",
        module="delivery",
        target_type="store",
        target_id=int(store_id),
        detail={"store_id": int(store_id), "platform": data.platform, "results": results},
        request=request,
        store_id=int(store_id),
    )
    return success({"store_id": store_id, "results": results}, request=request)


@router.post("/stores/{store_id}/close", dependencies=[require_permission("product:update")])
async def close_delivery_store(store_id: int, data: StoreOpenPayload, request: Request, db: DbSession, user: CurrentUser):
    """手动关店（商家休息/暂停接单）。"""
    results = await change_store_open_status(db, user, store_id, "REST", data.platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.store.close",
        module="delivery",
        target_type="store",
        target_id=int(store_id),
        detail={"store_id": int(store_id), "platform": data.platform, "results": results},
        request=request,
        store_id=int(store_id),
    )
    return success({"store_id": store_id, "results": results}, request=request)


@router.patch(
    "/store-bindings/{binding_id}/auto-sync-open",
    dependencies=[require_permission("product:update")],
)
async def set_binding_auto_sync(
    binding_id: int, data: AutoSyncOpenPayload, request: Request, db: DbSession, user: CurrentUser
):
    """开启/关闭该绑定的按营业时间自动开/关店（默认关闭）。"""
    from app.services.store_scope import assert_store_access

    row = await db.get(PlatformStoreBinding, binding_id)
    if not row:
        raise AppException(40450, "绑定不存在", status_code=404)
    assert_store_access(user, row.store_id)
    row.auto_sync_open = bool(data.auto_sync_open)
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.binding.auto_sync_open",
        module="delivery",
        target_type="platform_store_binding",
        target_id=int(row.id),
        detail={"store_id": int(row.store_id) if row.store_id else None, "auto_sync_open": bool(row.auto_sync_open)},
        request=request,
        store_id=int(row.store_id) if row.store_id else None,
    )
    return success({"id": row.id, "auto_sync_open": row.auto_sync_open}, request=request)

"""餐品口味画像手工维护与整店重算：从 products 路由拆出，路径仍挂在 /api。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import success
from app.models import Product, Store
from app.services.audit_service import AuditService
from app.services.recommendation_service import invalidate_store_local_caches
from app.services.store_scope import assert_store_access
from app.services.taste_profile_service import profile_payload, recompute_store, upsert_manual

router = APIRouter(tags=["餐品"])


class TasteProfileManualUpdate(BaseModel):
    dimensions: dict[str, int] | None = None
    tags_add: list[str] = Field(default_factory=list)
    tags_remove: list[str] = Field(default_factory=list)
    reset_dimensions: list[str] = Field(default_factory=list)
    reset_tags: list[str] = Field(default_factory=list)


class TasteProfileRebuildRequest(BaseModel):
    store_id: int | None = None


@router.put("/products/{product_id}/taste-profile", dependencies=[require_permission("product:update")])
async def update_product_taste_profile(
    product_id: int,
    data: TasteProfileManualUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    product = await db.get(Product, product_id)
    if not product:
        return success(None, message="餐品不存在", request=request)
    assert_store_access(user, product.store_id)
    profile = await upsert_manual(
        db,
        product_id,
        dimensions=data.dimensions,
        tags_add=data.tags_add,
        tags_remove=data.tags_remove,
        reset_dimensions=data.reset_dimensions,
        reset_tags=data.reset_tags,
    )
    # 口味画像只影响推荐池/协同过滤缓存，不改变菜单展示内容，不递增菜单代际。\n    invalidate_store_local_caches(int(product.store_id))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.taste_profile.update",
        module="product",
        target_type="product",
        target_id=product_id,
        detail={
            "dimensions": data.dimensions or {},
            "tags_add": data.tags_add,
            "tags_remove": data.tags_remove,
            "reset_dimensions": data.reset_dimensions,
            "reset_tags": data.reset_tags,
        },
        request=request,
        store_id=int(product.store_id),
    )
    return success(profile_payload(profile), request=request)


@router.post("/products/taste-profiles/rebuild", dependencies=[require_permission("product:update")])
async def rebuild_store_taste_profiles(
    data: TasteProfileRebuildRequest,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """整店口味画像 AUTO 重算（保留 MANUAL 接管键）。超管需显式指定 store_id。"""
    store_id = data.store_id
    if user.role.code == "SUPER_ADMIN":
        if not store_id:
            raise AppException(40001, "请指定需要重算的门店")
    else:
        if not user.store_id:
            raise AppException(40301, "账号未绑定门店", status_code=403)
        store_id = int(user.store_id)
    store_id = int(store_id)
    store = await db.get(Store, store_id)
    if not store:
        raise AppException(40004, "门店不存在")
    rebuilt = await recompute_store(db, store_id)
    # 口味画像只影响推荐缓存；菜单版本由真正的菜单/库存事务通过 Outbox 推进。\n    invalidate_store_local_caches(store_id)
    await AuditService.log_staff(
        db,
        user=user,
        action="product.taste_profile.rebuild",
        module="product",
        target_type="store",
        target_id=store_id,
        detail={"store_id": store_id, "rebuilt": rebuilt},
        request=request,
        store_id=store_id,
    )
    return success({"store_id": store_id, "rebuilt": rebuilt}, request=request)

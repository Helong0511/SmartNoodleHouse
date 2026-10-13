"""社会贡献公益 API。"""
from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Query, Request

from app.api.deps import (
    CurrentCustomer,
    CurrentUser,
    DbSession,
    OptionalCustomer,
    require_permission,
    require_pos,
    require_social_identity_manage,
)
from app.core.exceptions import AppException
from app.core.response import calc_pages, success
from app.services.audit_service import AuditService
from app.services.social_benefit_service import SocialBenefitService
from app.services.store_scope import assert_store_access, require_bound_store

router = APIRouter(prefix="/social-benefits", tags=["社会贡献"])


class ApplyIdentityIn(BaseModel):
    identity_code: str = Field(..., min_length=2, max_length=32)
    store_id: int | None = None
    credential_note: str | None = Field(default=None, max_length=255)
    credential_media_url: str | None = Field(default=None, max_length=500)


class PosVerifyIn(BaseModel):
    customer_id: int = Field(..., ge=1)
    identity_code: str = Field(..., min_length=2, max_length=32)
    store_id: int | None = None
    credential_note: str | None = Field(default=None, max_length=255)
    activate: bool = True


class ReviewIn(BaseModel):
    approve: bool
    reject_reason: str | None = Field(default=None, max_length=255)


class RevokeIn(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class PreviewIn(BaseModel):
    customer_id: int | None = Field(default=None, ge=1)
    store_id: int = Field(..., ge=1)
    subtotal: str = Field(..., description="商品小计")
    discountable_subtotal: str | None = None
    packing_fee: str = "0"
    product_ids: list[int] = Field(default_factory=list)
    category_ids: list[int] = Field(default_factory=list)


@router.get("/identity-types")
async def identity_types(request: Request, db: DbSession):
    items = await SocialBenefitService.list_identity_types(db)
    return success({"items": items}, request=request)


def _mini_verify_method(request: Request) -> str:
    """小程序申请落库：按 JWT channel 区分微信/支付宝；未知则回退 MINI。"""
    ch = str(getattr(request.state, "customer_channel", "") or "").strip().lower()
    if ch == "wechat":
        return "WECHAT"
    if ch == "alipay":
        return "ALIPAY"
    return "MINI"


@router.post("/identities/apply")
async def apply_identity(
    data: ApplyIdentityIn, request: Request, db: DbSession, customer: CurrentCustomer
):
    row = await SocialBenefitService.apply_identity(
        db,
        customer_id=int(customer.id),
        identity_code=data.identity_code.strip().upper(),
        store_id=data.store_id,
        verify_method=_mini_verify_method(request),
        credential_note=data.credential_note,
        credential_media_url=data.credential_media_url,
    )
    # 先序列化再 commit：避免提交后惰性加载 created_at 触发 MissingGreenlet
    payload = SocialBenefitService._identity_payload(row)
    await db.commit()
    return success(payload, request=request)


@router.get("/identities/mine")
async def mine_identities(request: Request, db: DbSession, customer: CurrentCustomer):
    items = await SocialBenefitService.mine_identities(db, int(customer.id))
    return success({"items": items}, request=request)


@router.get("/identities", dependencies=[require_permission("customer:read")])
async def list_identities(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    status: str | None = None,
    customer_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    store_id = require_bound_store(user, None)
    items, total = await SocialBenefitService.list_identities(
        db,
        store_id=store_id,
        status=status,
        customer_id=customer_id,
        page=page,
        page_size=page_size,
    )
    return success(
        {"items": items, "total": total, "page": page, "pages": calc_pages(total, page_size)},
        request=request,
    )


@router.post("/identities/{identity_id}/review", dependencies=[require_social_identity_manage()])
async def review_identity(
    identity_id: int,
    data: ReviewIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    store_id = require_bound_store(user, None)
    row = await SocialBenefitService.review_identity(
        db,
        identity_id=identity_id,
        approve=bool(data.approve),
        reviewer_id=int(user.id),
        reject_reason=data.reject_reason,
        store_id=store_id,
    )
    payload = SocialBenefitService._identity_payload(row, staff=True)
    await AuditService.log(
        db,
        action="social.identity.review",
        module="social",
        user_id=user.id,
        target_type="customer_social_identity",
        target_id=int(row.id),
        detail={"approve": data.approve, "status": row.status},
        request=request,
        store_id=row.store_id,
    )
    await db.commit()
    return success(payload, request=request)


@router.post("/identities/{identity_id}/revoke", dependencies=[require_social_identity_manage()])
async def revoke_identity(
    identity_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    data: RevokeIn = RevokeIn(),
):
    """取消顾客公益身份：已生效→REVOKED，待审→REJECTED。"""
    store_id = require_bound_store(user, None)
    reason = data.reason
    row = await SocialBenefitService.revoke_identity(
        db,
        identity_id=identity_id,
        operator_id=int(user.id),
        store_id=store_id,
        reason=reason,
    )
    payload = SocialBenefitService._identity_payload(row, staff=True)
    await AuditService.log(
        db,
        action="social.identity.revoke",
        module="social",
        user_id=user.id,
        target_type="customer_social_identity",
        target_id=int(row.id),
        detail={"status": row.status, "reason": row.reject_reason},
        request=request,
        store_id=row.store_id,
    )
    await db.commit()
    return success(payload, request=request)


@router.post("/identities/pos-verify", dependencies=[require_pos()])
async def pos_verify(data: PosVerifyIn, request: Request, db: DbSession, user: CurrentUser):
    store_id = require_bound_store(user, data.store_id)
    if store_id is None:
        raise AppException(40008, "请选择门店")
    assert_store_access(user, int(store_id))
    row = await SocialBenefitService.pos_verify(
        db,
        customer_id=int(data.customer_id),
        identity_code=data.identity_code.strip().upper(),
        store_id=int(store_id),
        operator_user_id=int(user.id),
        credential_note=data.credential_note,
        activate=bool(data.activate),
    )
    await db.refresh(row)
    payload = SocialBenefitService._identity_payload(row, staff=True)
    await AuditService.log(
        db,
        action="social.identity.pos_verify",
        module="social",
        user_id=user.id,
        target_type="customer_social_identity",
        target_id=int(row.id),
        detail={"identity_code": row.identity_code, "status": row.status},
        request=request,
        store_id=int(store_id),
    )
    await db.commit()
    return success(payload, request=request)


@router.post("/preview")
async def preview(
    data: PreviewIn,
    request: Request,
    db: DbSession,
    customer: OptionalCustomer,
):
    """预览公益折扣。有顾客 JWT 时强制本人；否则须显式传 customer_id（如 POS）。"""
    from decimal import Decimal

    from app.core.response import money

    cid = int(data.customer_id) if data.customer_id else None
    if customer is not None:
        cid = int(customer.id)
    if cid is None:
        raise AppException(40008, "请指定顾客")
    subtotal = money(Decimal(str(data.subtotal)))
    disc = money(
        Decimal(
            str(
                data.discountable_subtotal
                if data.discountable_subtotal is not None
                else data.subtotal
            )
        )
    )
    pack = money(Decimal(str(data.packing_fee or "0")))
    result = await SocialBenefitService.preview(
        db,
        customer_id=cid,
        store_id=int(data.store_id),
        subtotal=subtotal,
        discountable_subtotal=disc,
        packing_fee=pack,
        product_ids=data.product_ids,
        category_ids=data.category_ids,
    )
    return success(result, request=request)


@router.get("/rules", dependencies=[require_permission("customer:read")])
async def list_rules(request: Request, db: DbSession, user: CurrentUser):
    store_id = require_bound_store(user, None)
    items = await SocialBenefitService.list_rules(db, store_id=store_id)
    return success({"items": items}, request=request)


@router.get("/redemptions", dependencies=[require_permission("customer:read")])
async def list_redemptions(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    store_id = require_bound_store(user, None)
    items, total = await SocialBenefitService.list_redemptions(
        db, store_id=store_id, page=page, page_size=page_size
    )
    return success(
        {"items": items, "total": total, "page": page, "pages": calc_pages(total, page_size)},
        request=request,
    )


@router.get("/stats", dependencies=[require_permission("customer:read")])
async def social_stats(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    year: int | None = None,
    month: int | None = None,
):
    """公益看板：月人次 / 补贴 / 身份分布 / 待审数。"""
    store_id = require_bound_store(user, None)
    data = await SocialBenefitService.monthly_stats(
        db, store_id=store_id, year=year, month=month
    )
    return success(data, request=request)

"""后台营销中心：限量免费抢购配置、参与门店与核销。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import calc_pages, success
from app.models import MarketingCampaign, User
from app.services.audit_service import AuditService
from app.services.flash_sale_admin_service import FlashSaleAdminService
from app.services.flash_sale_service import FlashSaleService
from app.services.marketing_service import MarketingService
from app.services.store_scope import assert_store_access, require_bound_store

router = APIRouter(prefix="/marketing/flash-sales", tags=["营销中心-限量抢购"])


class FlashSaleQuotaIn(BaseModel):
    store_id: int = Field(..., ge=1)
    product_id: int | None = Field(default=None, ge=1)
    total_quantity: int = Field(default=0, ge=0, le=100000)


class FlashSaleConfigIn(BaseModel):
    product_id: int = Field(..., ge=1)
    per_customer_limit: int = Field(default=1, ge=1, le=20)
    claim_valid_minutes: int = Field(default=30, ge=1, le=1440)
    verify_deadline_at: datetime | None = None
    notice: str | None = Field(default=None, max_length=200)
    store_quotas: list[FlashSaleQuotaIn] = Field(default_factory=list)


class ParticipationIn(BaseModel):
    excluded_store_ids: list[int] = Field(default_factory=list)


class VerifyIn(BaseModel):
    claim_no: str = Field(..., min_length=4, max_length=40)
    store_id: int | None = Field(default=None, ge=1)


def _assert_campaign_access(user: User, camp: MarketingCampaign) -> None:
    """总部活动仅超管可配置；门店活动按门店归属校验。"""
    if camp.store_id is None:
        role_code = getattr(getattr(user, "role", None), "code", None)
        if role_code != "SUPER_ADMIN":
            raise AppException(40301, "总部活动仅超管可配置", status_code=403)
        return
    assert_store_access(user, camp.store_id)


@router.get("/campaigns/{campaign_id}", dependencies=[require_permission("coupon:read")])
async def flash_sale_config_detail(
    campaign_id: int, request: Request, db: DbSession, user: CurrentUser
):
    camp = await MarketingService.get_campaign(db, campaign_id)
    _assert_campaign_access(user, camp)
    data = await FlashSaleAdminService.get_detail(db, campaign_id)
    return success(data, request=request)


@router.put("/campaigns/{campaign_id}/config", dependencies=[require_permission("coupon:manage")])
async def upsert_flash_sale_config(
    campaign_id: int,
    data: FlashSaleConfigIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    camp = await MarketingService.get_campaign(db, campaign_id)
    _assert_campaign_access(user, camp)
    result = await FlashSaleAdminService.upsert_config(
        db,
        campaign_id=campaign_id,
        product_id=data.product_id,
        per_customer_limit=data.per_customer_limit,
        claim_valid_minutes=data.claim_valid_minutes,
        verify_deadline_at=data.verify_deadline_at,
        notice=data.notice,
        store_quotas=[q.model_dump() for q in data.store_quotas],
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="marketing.flash_sale.config",
        module="marketing",
        target_type="marketing_campaign",
        target_id=campaign_id,
        detail={
            "product_id": data.product_id,
            "per_customer_limit": data.per_customer_limit,
            "claim_valid_minutes": data.claim_valid_minutes,
            "stores": [q.store_id for q in data.store_quotas],
        },
        request=request,
        store_id=camp.store_id,
    )
    await db.commit()
    return success(result, request=request)


@router.put(
    "/campaigns/{campaign_id}/participation",
    dependencies=[require_permission("coupon:manage")],
)
async def set_flash_sale_participation(
    campaign_id: int,
    data: ParticipationIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    camp = await MarketingService.get_campaign(db, campaign_id)
    if camp.store_id is not None:
        raise AppException(40014, "门店活动无需配置参与范围")
    role_code = getattr(getattr(user, "role", None), "code", None)
    if role_code != "SUPER_ADMIN":
        raise AppException(40301, "总部活动参与范围仅超管可配置", status_code=403)
    result = await FlashSaleAdminService.set_participation(
        db, campaign_id=campaign_id, excluded_store_ids=data.excluded_store_ids
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="marketing.flash_sale.participation",
        module="marketing",
        target_type="marketing_campaign",
        target_id=campaign_id,
        detail=result,
        request=request,
        store_id=None,
    )
    await db.commit()
    return success(result, request=request)


@router.get("/claims", dependencies=[require_permission("coupon:read")])
async def list_flash_sale_claims(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = Query(default=None, description="超管按门店筛选；本店账号忽略"),
    campaign_id: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    sid = require_bound_store(user, store_id)
    items, total = await FlashSaleAdminService.list_claims(
        db,
        store_id=sid,
        campaign_id=campaign_id,
        status=status,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return success(
        {
            "items": items,
            "total": total,
            "page": page,
            "pages": calc_pages(total, page_size),
            "filter_store_id": sid,
        },
        request=request,
    )


@router.post("/claims/verify", dependencies=[require_permission("coupon:manage")])
async def verify_flash_sale_claim(
    data: VerifyIn, request: Request, db: DbSession, user: CurrentUser
):
    # 非超管强制本店，忽略前端传入的其他 store_id
    sid = require_bound_store(user, data.store_id)
    result = await FlashSaleService.verify(
        db,
        claim_no=data.claim_no,
        store_id=sid,
        operator_id=int(user.id),
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="marketing.flash_sale.verify",
        module="marketing",
        target_type="flash_sale_claim",
        target_id=result["claim_id"],
        detail={
            "claim_no": result["claim_no"],
            "order_no": result["order_no"],
            "already_verified": result["already_verified"],
            "store_id": result["store_id"],
        },
        request=request,
        store_id=result["store_id"],
    )
    await db.commit()
    if not result["already_verified"]:
        # 免费核销订单已提交后通知 KDS/订单屏/顾客端；推送失败不回滚已核销订单。
        try:
            from app.services.websocket_manager import ws_manager

            await ws_manager.notify_order_event(
                "ORDER_CREATED",
                {
                    "order_id": int(result["order_id"]),
                    "order_no": str(result["order_no"]),
                    "status": "PAID",
                    "store_id": int(result["store_id"]),
                    "table_id": None,
                    "table_no": None,
                    "dining_type": "TAKEAWAY",
                    "total_amount": "0.00",
                    "payment_method": "FREE",
                },
                customer_id=int(result["customer_id"]),
            )
        except Exception:
            import logging

            logging.getLogger("app.flash_sale").warning(
                "免费抢购订单 WS 通知失败 order_id=%s", result["order_id"], exc_info=True
            )
        # 核销事务已提交后才允许播报；播报失败只记日志，不回滚核销
        await FlashSaleService.announce_verified(
            db,
            store_id=int(result["store_id"]),
            customer_id=int(result["customer_id"]),
            product_name=str(result["product_name"]),
        )
    return success(result, request=request)

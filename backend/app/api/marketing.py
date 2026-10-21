"""营销中心：分群 / 活动 / 任务。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import calc_pages, success
from app.services.audit_service import AuditService
from app.services.marketing_service import MarketingService
from app.services.segment_service import SegmentService
from app.services.store_scope import require_bound_store

router = APIRouter(prefix="/marketing", tags=["营销中心"])


class SegmentUpsertIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    segment_type: str = Field(default="DYNAMIC", max_length=20)
    rules_json: dict | list | None = None
    status: int = Field(default=1, ge=0, le=1)
    store_id: int | None = None


class EstimateIn(BaseModel):
    rules_json: dict | list | None = None
    store_id: int | None = None
    sample_limit: int = Field(default=20, ge=0, le=50)


class CampaignUpsertIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    campaign_type: str = Field(default="MEMBER_DAY", max_length=30)
    segment_id: int | None = None
    coupon_template_id: int | None = None
    schedule_mode: str = Field(default="ABSOLUTE", max_length=20, description="ABSOLUTE|ANNUAL_MD")
    schedule_calendar: str = Field(default="SOLAR", max_length=10, description="SOLAR|LUNAR")
    annual_start_month: int | None = Field(default=None, ge=1, le=12)
    annual_start_day: int | None = Field(default=None, ge=1, le=31)
    annual_end_month: int | None = Field(default=None, ge=1, le=12)
    annual_end_day: int | None = Field(default=None, ge=1, le=31)
    start_at: datetime | None = None
    end_at: datetime | None = None
    budget_cap: Decimal | None = None
    weekly_cap_override: int | None = Field(default=None, ge=0, le=100)
    boost_product_ids: list[int] | None = None
    remark: str | None = Field(default=None, max_length=500)
    store_id: int | None = None


class StatusIn(BaseModel):
    status: str = Field(..., min_length=2, max_length=20)


# ---- segments ----


@router.get("/segments", dependencies=[require_permission("customer:read")])
async def list_segments(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = Query(default=None, description="超管按门店筛选；本店账号忽略"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    sid = require_bound_store(user, store_id)
    items, total = await SegmentService.list_segments(
        db, store_id=sid, page=page, page_size=page_size
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


@router.post("/segments", dependencies=[require_permission("coupon:manage")])
async def create_segment(
    data: SegmentUpsertIn, request: Request, db: DbSession, user: CurrentUser
):
    store_id = require_bound_store(user, data.store_id)
    row = await SegmentService.upsert(
        db,
        store_id=store_id,
        code=data.code,
        name=data.name,
        segment_type=data.segment_type,
        rules_json=data.rules_json,
        status=data.status,
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="marketing.segment.create",
        module="marketing",
        target_type="customer_segment",
        target_id=int(row.id),
        detail={"code": row.code},
    )
    await db.commit()
    await db.refresh(row)
    return success(SegmentService._payload(row), request=request)


@router.put("/segments/{segment_id}", dependencies=[require_permission("coupon:manage")])
async def update_segment(
    segment_id: int, data: SegmentUpsertIn, request: Request, db: DbSession, user: CurrentUser
):
    store_id = require_bound_store(user, data.store_id)
    row = await SegmentService.upsert(
        db,
        segment_id=segment_id,
        store_id=store_id,
        code=data.code,
        name=data.name,
        segment_type=data.segment_type,
        rules_json=data.rules_json,
        status=data.status,
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="marketing.segment.update",
        module="marketing",
        target_type="customer_segment",
        target_id=int(row.id),
        detail={"code": row.code},
    )
    await db.commit()
    await db.refresh(row)
    return success(SegmentService._payload(row), request=request)


@router.post("/segments/estimate", dependencies=[require_permission("customer:read")])
async def estimate_segment(data: EstimateIn, request: Request, db: DbSession, user: CurrentUser):
    store_id = require_bound_store(user, data.store_id)
    result = await SegmentService.estimate(
        db,
        rules_json=data.rules_json,
        store_id=store_id,
        sample_limit=data.sample_limit,
    )
    return success(result, request=request)


@router.post("/segments/{segment_id}/materialize", dependencies=[require_permission("coupon:manage")])
async def materialize_segment(
    segment_id: int, request: Request, db: DbSession, user: CurrentUser
):
    seg = await SegmentService.get_segment(db, segment_id)
    require_bound_store(user, seg.store_id)
    # 总部快照全局共用；门店账号物化会覆盖他店发布依赖，禁止
    if seg.store_id is None and getattr(getattr(user, "role", None), "code", None) != "SUPER_ADMIN":
        raise AppException(
            40301,
            "总部预置分群仅超管可物化；门店发布营销活动时会按本店自动圈人，无需本地物化",
        )
    result = await SegmentService.materialize(db, segment_id)
    await AuditService.log_staff(
        db,
        user=user,
        action="marketing.segment.materialize",
        module="marketing",
        target_type="customer_segment",
        target_id=segment_id,
        detail=result,
    )
    await db.commit()
    return success(result, request=request)


# ---- campaigns ----


@router.get("/campaigns", dependencies=[require_permission("coupon:read")])
async def list_campaigns(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = Query(default=None, description="超管按门店筛选；本店账号忽略"),
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    sid = require_bound_store(user, store_id)
    items, total = await MarketingService.list_campaigns(
        db, store_id=sid, status=status, page=page, page_size=page_size
    )
    enabled = await MarketingService.is_enabled(db, sid)
    return success(
        {
            "items": items,
            "total": total,
            "page": page,
            "pages": calc_pages(total, page_size),
            "filter_store_id": sid,
            "marketing_enabled": enabled,
        },
        request=request,
    )


@router.post("/campaigns", dependencies=[require_permission("coupon:manage")])
async def create_campaign(
    data: CampaignUpsertIn, request: Request, db: DbSession, user: CurrentUser
):
    store_id = require_bound_store(user, data.store_id)
    row = await MarketingService.upsert_campaign(
        db,
        store_id=store_id,
        code=data.code,
        name=data.name,
        campaign_type=data.campaign_type,
        segment_id=data.segment_id,
        coupon_template_id=data.coupon_template_id,
        schedule_mode=data.schedule_mode,
        schedule_calendar=data.schedule_calendar,
        annual_start_month=data.annual_start_month,
        annual_start_day=data.annual_start_day,
        annual_end_month=data.annual_end_month,
        annual_end_day=data.annual_end_day,
        start_at=data.start_at,
        end_at=data.end_at,
        budget_cap=data.budget_cap,
        weekly_cap_override=data.weekly_cap_override,
        boost_product_ids=data.boost_product_ids,
        remark=data.remark,
        created_by=int(user.id),
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="marketing.campaign.create",
        module="marketing",
        target_type="marketing_campaign",
        target_id=int(row.id),
        detail={"code": row.code},
    )
    await db.commit()
    await db.refresh(row)
    return success(await MarketingService.payload_for(db, row), request=request)


@router.put("/campaigns/{campaign_id}", dependencies=[require_permission("coupon:manage")])
async def update_campaign(
    campaign_id: int, data: CampaignUpsertIn, request: Request, db: DbSession, user: CurrentUser
):
    store_id = require_bound_store(user, data.store_id)
    row = await MarketingService.upsert_campaign(
        db,
        campaign_id=campaign_id,
        store_id=store_id,
        code=data.code,
        name=data.name,
        campaign_type=data.campaign_type,
        segment_id=data.segment_id,
        coupon_template_id=data.coupon_template_id,
        schedule_mode=data.schedule_mode,
        schedule_calendar=data.schedule_calendar,
        annual_start_month=data.annual_start_month,
        annual_start_day=data.annual_start_day,
        annual_end_month=data.annual_end_month,
        annual_end_day=data.annual_end_day,
        start_at=data.start_at,
        end_at=data.end_at,
        budget_cap=data.budget_cap,
        weekly_cap_override=data.weekly_cap_override,
        boost_product_ids=data.boost_product_ids,
        remark=data.remark,
        created_by=int(user.id),
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="marketing.campaign.update",
        module="marketing",
        target_type="marketing_campaign",
        target_id=int(row.id),
        detail={"code": row.code},
    )
    await db.commit()
    await db.refresh(row)
    return success(await MarketingService.payload_for(db, row), request=request)


@router.post("/campaigns/{campaign_id}/publish", dependencies=[require_permission("coupon:manage")])
async def publish_campaign(
    campaign_id: int, request: Request, db: DbSession, user: CurrentUser
):
    camp = await MarketingService.get_campaign(db, campaign_id)
    require_bound_store(user, camp.store_id)
    result = await MarketingService.publish(db, campaign_id)
    await AuditService.log_staff(
        db,
        user=user,
        action="marketing.campaign.publish",
        module="marketing",
        target_type="marketing_campaign",
        target_id=campaign_id,
        detail={"enqueued": result.get("enqueued"), "status": result["campaign"]["status"]},
    )
    await db.commit()
    return success(result, request=request)


@router.post("/campaigns/{campaign_id}/status", dependencies=[require_permission("coupon:manage")])
async def set_campaign_status(
    campaign_id: int, data: StatusIn, request: Request, db: DbSession, user: CurrentUser
):
    camp = await MarketingService.get_campaign(db, campaign_id)
    require_bound_store(user, camp.store_id)
    row = await MarketingService.set_status(db, campaign_id, data.status)
    await AuditService.log_staff(
        db,
        user=user,
        action="marketing.campaign.status",
        module="marketing",
        target_type="marketing_campaign",
        target_id=campaign_id,
        detail={"status": row.status},
    )
    await db.commit()
    await db.refresh(row)
    return success(await MarketingService.payload_for(db, row), request=request)


@router.get("/campaigns/{campaign_id}/tasks", dependencies=[require_permission("coupon:read")])
async def list_campaign_tasks(
    campaign_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    camp = await MarketingService.get_campaign(db, campaign_id)
    require_bound_store(user, camp.store_id)
    items, total, task_stats = await MarketingService.list_tasks(
        db, campaign_id=campaign_id, status=status, page=page, page_size=page_size
    )
    return success(
        {
            "items": items,
            "total": total,
            "page": page,
            "pages": calc_pages(total, page_size),
            "task_stats": task_stats,
        },
        request=request,
    )


@router.post(
    "/campaigns/{campaign_id}/tasks/retry-failed",
    dependencies=[require_permission("coupon:manage")],
)
async def retry_failed_tasks(
    campaign_id: int, request: Request, db: DbSession, user: CurrentUser
):
    camp = await MarketingService.get_campaign(db, campaign_id)
    require_bound_store(user, camp.store_id)
    result = await MarketingService.retry_failed(db, campaign_id)
    await db.commit()
    return success(result, request=request)


@router.get("/reports/overview", dependencies=[require_permission("coupon:read")])
async def marketing_report_overview(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = Query(default=None, description="超管按门店筛选；本店账号忽略"),
):
    """活动核销率 / 归因实收 / ROI（无明文手机）。"""
    sid = require_bound_store(user, store_id)
    data = await MarketingService.report_overview(db, store_id=sid)
    data["filter_store_id"] = sid
    data["marketing_enabled"] = await MarketingService.is_enabled(db, sid)
    return success(data, request=request)

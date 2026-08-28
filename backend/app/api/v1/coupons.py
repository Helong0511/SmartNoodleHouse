from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import calc_pages, success
from app.models import CouponTemplate
from app.schemas.coupon import CouponIssueRequest, CouponTemplateCreate, CouponTemplateUpdate
from app.services.coupon_service import CouponService
from app.services.customer_service import CustomerService

router = APIRouter(prefix="/coupons", tags=["优惠券"])


class TemplateStatusUpdate(BaseModel):
    status: int = Field(..., ge=0, le=1)


@router.get("/templates", dependencies=[require_permission("coupon:read")])
async def list_templates(request: Request, db: DbSession, user: CurrentUser, all: bool = True):
    """优惠券模板列表。"""
    items = await CouponService.list_templates(db, include_disabled=all)
    return success({"items": items}, request=request)


@router.post("/templates", dependencies=[require_permission("coupon:manage")])
async def create_template(data: CouponTemplateCreate, request: Request, db: DbSession, user: CurrentUser):
    tmpl = await CouponService.create_template(db, data.model_dump())
    await db.commit()
    return success(CouponService.template_payload(tmpl), request=request)


@router.put("/templates/{template_id}", dependencies=[require_permission("coupon:manage")])
async def update_template(
    template_id: int,
    data: CouponTemplateUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    tmpl = await db.get(CouponTemplate, template_id)
    if not tmpl:
        raise AppException(40407, "优惠券模板不存在")
    payload = {k: v for k, v in data.model_dump().items() if v is not None}
    tmpl = await CouponService.update_template(db, tmpl, payload)
    await db.commit()
    return success(CouponService.template_payload(tmpl), request=request)


@router.patch("/templates/{template_id}/status", dependencies=[require_permission("coupon:manage")])
async def set_template_status(
    template_id: int,
    data: TemplateStatusUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    tmpl = await db.get(CouponTemplate, template_id)
    if not tmpl:
        raise AppException(40407, "优惠券模板不存在")
    tmpl.status = int(data.status)
    await db.commit()
    return success(CouponService.template_payload(tmpl), request=request)


@router.get("/issued", dependencies=[require_permission("coupon:read")])
async def list_issued_coupons(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    customer_id: int | None = None,
    template_id: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    """顾客持券记录（全状态）。"""
    items, total = await CouponService.list_customer_coupons_admin(
        db,
        customer_id=customer_id,
        template_id=template_id,
        status=status,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return success(
        {
            "items": items,
            "total": total,
            "page": max(1, page),
            "page_size": min(max(1, page_size), 100),
            "total_pages": calc_pages(total, page_size),
        },
        request=request,
    )


@router.get("/customers/search", dependencies=[require_permission("coupon:manage")])
async def search_customers_for_issue(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    keyword: str = Query(..., min_length=1, max_length=100, description="昵称/手机/顾客编号"),
    page_size: int = 20,
):
    """手动发券：搜索全库顾客（不受本店下过单限制）。"""
    rows, _total = await CustomerService.search_active_customers(
        db, keyword, page=1, page_size=page_size
    )
    items = [
        {
            "id": c.id,
            "customer_no": c.customer_no,
            "display_name": c.display_name or c.nickname,
            "nickname": c.nickname,
            "phone": c.phone,
        }
        for c in rows
    ]
    return success({"items": items}, request=request)


@router.post("/issue", dependencies=[require_permission("coupon:manage")])
async def issue_coupon(data: CouponIssueRequest, request: Request, db: DbSession, user: CurrentUser):
    """向指定顾客发放优惠券/代金券，可指定张数。"""
    issued = await CouponService.issue_by_template_id(
        db, data.customer_id, data.template_id, quantity=data.quantity
    )
    await db.commit()
    tmpl = await db.get(CouponTemplate, data.template_id)
    kind = "代金券" if tmpl and CouponService.is_voucher_type(tmpl.coupon_type) else "优惠券"
    items = [
        {
            "id": cc.id,
            "coupon_no": cc.coupon_no,
            "customer_id": cc.customer_id,
            "template_id": cc.template_id,
            "template_name": tmpl.name if tmpl else None,
            "summary": CouponService.summary_label(tmpl) if tmpl else None,
            "status": cc.status,
            "expires_at": cc.expires_at.isoformat() if cc.expires_at else None,
        }
        for cc in issued
    ]
    qty = len(items)
    return success(
        {
            "quantity": qty,
            "items": items,
            "template_name": tmpl.name if tmpl else None,
            "coupon_type": tmpl.coupon_type if tmpl else None,
        },
        message=f"已发放 {qty} 张{kind}",
        request=request,
    )

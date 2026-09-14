from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import calc_pages, success
from app.models import CouponTemplate, Customer, Order
from app.schemas.coupon import CouponIssueRequest, CouponTemplateCreate, CouponTemplateUpdate
from app.services.coupon_service import CouponService
from app.services.customer_service import CustomerService
from app.services.store_scope import require_bound_store

router = APIRouter(prefix="/coupons", tags=["优惠券"])


class TemplateStatusUpdate(BaseModel):
    status: int = Field(..., ge=0, le=1)


def _assert_template_manage_access(user, tmpl: CouponTemplate) -> None:
    """门店账号仅可改本店模板；总部模板仅超管可改。"""
    if user.role.code == "SUPER_ADMIN":
        return
    bound = require_bound_store(user, None)
    sid = getattr(tmpl, "store_id", None)
    if sid is None:
        raise AppException(40301, "无权修改总部优惠券模板")
    if bound is None or int(sid) != int(bound):
        raise AppException(40301, "无权修改其他门店优惠券模板")


@router.get("/templates", dependencies=[require_permission("coupon:read")])
async def list_templates(request: Request, db: DbSession, user: CurrentUser, all: bool = True):
    """优惠券模板列表：门店见「总部 ∪ 本店」；超管见全部。"""
    store_id = require_bound_store(user, None)
    items = await CouponService.list_templates(db, include_disabled=all, store_id=store_id)
    return success({"items": items}, request=request)


@router.post("/templates", dependencies=[require_permission("coupon:manage")])
async def create_template(data: CouponTemplateCreate, request: Request, db: DbSession, user: CurrentUser):
    payload = data.model_dump()
    if user.role.code == "SUPER_ADMIN":
        # 超管可建总部(NULL)或指定店
        pass
    else:
        bound = require_bound_store(user, None)
        if bound is None:
            raise AppException(40301, "账号未绑定门店，无法创建模板")
        payload["store_id"] = int(bound)
    tmpl = await CouponService.create_template(db, payload)
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
    _assert_template_manage_access(user, tmpl)
    raw = data.model_dump(exclude_unset=True)
    if user.role.code != "SUPER_ADMIN":
        raw.pop("store_id", None)  # 门店不可改归属
    tmpl = await CouponService.update_template(db, tmpl, raw)
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
    _assert_template_manage_access(user, tmpl)
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
    """顾客持券记录（全状态）。门店账号仅看本店相关顾客。"""
    store_id = require_bound_store(user, None)
    items, total = await CouponService.list_customer_coupons_admin(
        db,
        customer_id=customer_id,
        template_id=template_id,
        status=status,
        keyword=keyword,
        page=page,
        page_size=page_size,
        store_id=store_id,
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
    """手动发券：按门店隔离搜索顾客（本店下过单或本店注册）。超管可搜全库。"""
    store_id = require_bound_store(user, None)
    rows, _total = await CustomerService.search_active_customers(
        db, keyword, page=1, page_size=page_size, store_id=store_id
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
    """向指定顾客发放优惠券/代金券。门店仅可对本店顾客发「总部∪本店」模板。"""
    store_id = require_bound_store(user, None)
    if store_id is not None:
        customer = await db.get(Customer, data.customer_id)
        if not customer:
            raise AppException(40401, "顾客不存在")
        in_scope = await db.scalar(
            select(Customer.id).where(
                Customer.id == data.customer_id,
                or_(
                    Customer.registered_store_id == store_id,
                    Customer.id.in_(
                        select(Order.customer_id).where(
                            Order.store_id == store_id,
                            Order.customer_id.isnot(None),
                        )
                    ),
                ),
            )
        )
        if not in_scope:
            raise AppException(40301, "无权向其他门店顾客发券")
    tmpl = await db.get(CouponTemplate, data.template_id)
    if not tmpl:
        raise AppException(40407, "优惠券模板不存在")
    if not CouponService.template_visible_to_store(tmpl, store_id):
        raise AppException(40301, "无权使用其他门店的优惠券模板")
    issued = await CouponService.issue_by_template_id(
        db,
        data.customer_id,
        data.template_id,
        quantity=data.quantity,
        client_request_id=data.client_request_id,
        issued_by_user_id=int(user.id) if getattr(user, "id", None) else None,
        issued_at_store_id=store_id,
    )
    await db.commit()
    kind = "代金券" if CouponService.is_voucher_type(tmpl.coupon_type) else "优惠券"
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

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, require_permission
from app.core.config import get_settings
from app.core.response import calc_pages, success
from app.models import (
    Customer,
    CustomerAlipayAccount,
    CustomerPreference,
    CustomerWechatAccount,
    Order,
)
from app.services.customer_service import CustomerService, PreferenceService

router = APIRouter(prefix="/customers", tags=["顾客"])


@router.get("", dependencies=[require_permission("customer:read")])
async def list_customers(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
):
    q = select(Customer)
    if keyword:
        q = q.where(Customer.display_name.contains(keyword) | Customer.phone.contains(keyword))
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    result = await db.execute(q.order_by(Customer.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    items = []
    for c in result.scalars():
        order_count = await db.scalar(select(func.count()).select_from(Order).where(Order.customer_id == c.id)) or 0
        items.append({
            "id": c.id,
            "customer_no": c.customer_no,
            "display_name": c.display_name,
            "nickname": c.nickname,
            "phone": c.phone,
            "order_count": order_count,
            "last_order_at": c.last_order_at.isoformat() if c.last_order_at else None,
        })
    return success({"items": items, "total": total or 0, "page": page, "page_size": page_size}, request=request)


@router.get("/{customer_id}", dependencies=[require_permission("customer:read")])
async def get_customer(customer_id: int, request: Request, db: DbSession):
    customer = await db.get(Customer, customer_id)
    if not customer:
        return success(None, message="顾客不存在", request=request)
    wechat = await db.execute(select(CustomerWechatAccount).where(CustomerWechatAccount.customer_id == customer_id))
    alipay = await db.execute(select(CustomerAlipayAccount).where(CustomerAlipayAccount.customer_id == customer_id))
    prefs = await PreferenceService.get_recommendations(db, customer_id)
    return success(
        {
            "id": customer.id,
            "customer_no": customer.customer_no,
            "display_name": customer.display_name,
            "phone": customer.phone,
            "wechat_accounts": [{"openid": w.openid} for w in wechat.scalars()],
            "alipay_accounts": [{"alipay_user_id": a.alipay_user_id} for a in alipay.scalars()],
            "recommendations": prefs,
        },
        request=request,
    )


@router.get("/{customer_id}/preferences")
async def get_preferences(
    customer_id: int,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    if customer.id != customer_id:
        raise HTTPException(status_code=403, detail="无权查看他人偏好")
    prefs = await PreferenceService.get_recommendations(db, customer_id)
    result = await db.execute(
        select(CustomerPreference).where(CustomerPreference.customer_id == customer_id).order_by(
            CustomerPreference.preference_score.desc()
        )
    )
    prefs["all"] = [
        {"category": p.category, "option_code": p.option_code, "score": float(p.preference_score), "order_count": p.order_count}
        for p in result.scalars()
    ]
    return success(prefs, request=request)


# 已废弃：禁止客户端直接传 openid，请使用 /miniprogram/wechat/login
@router.post("/wechat/login")
async def wechat_login_deprecated():
    raise HTTPException(status_code=410, detail="已废弃，请使用 POST /api/v1/miniprogram/wechat/login 并传入 code")


@router.post("/alipay/login")
async def alipay_login_deprecated():
    raise HTTPException(status_code=410, detail="已废弃，请使用 POST /api/v1/miniprogram/alipay/login 并传入 auth_code")

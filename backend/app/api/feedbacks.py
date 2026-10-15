"""后台轻量评价列表（顾客订单结果页提交的标签评价）。"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.response import apply_date_range, calc_pages, serialize_dt, success
from app.models import Customer, Order, Product, ProductFeedback
from app.services.feedback_service import VERDICT_WHITELIST
from app.services.store_scope import require_bound_store

router = APIRouter(prefix="/product-feedbacks", tags=["餐品评价"])

_SENTIMENTS = {"POSITIVE", "NEGATIVE", "NEUTRAL"}


def _base_joins():
    return (
        select(ProductFeedback, Product.name, Order.order_no, Customer)
        .outerjoin(Product, Product.id == ProductFeedback.product_id)
        .outerjoin(Order, Order.id == ProductFeedback.order_id)
        .outerjoin(Customer, Customer.id == ProductFeedback.customer_id)
    )


def _count_joins():
    return (
        select(func.count())
        .select_from(ProductFeedback)
        .outerjoin(Product, Product.id == ProductFeedback.product_id)
        .outerjoin(Order, Order.id == ProductFeedback.order_id)
        .outerjoin(Customer, Customer.id == ProductFeedback.customer_id)
    )


@router.get("", dependencies=[require_permission("order:read")])
async def list_product_feedbacks(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    store_id: int | None = None,
    sentiment: str | None = Query(None, description="POSITIVE/NEGATIVE/NEUTRAL"),
    keyword: str | None = Query(None, description="餐品名 / 订单号 / 顾客名 / 顾客编号"),
    date_from: str | None = None,
    date_to: str | None = None,
):
    """门店隔离的评价列表；超管可筛选门店，门店账号仅本店。"""
    sid = require_bound_store(user, store_id)
    q = _base_joins()
    cq = _count_joins()

    if sid is not None:
        q = q.where(ProductFeedback.store_id == sid)
        cq = cq.where(ProductFeedback.store_id == sid)

    if sentiment:
        st = sentiment.strip().upper()
        if st in _SENTIMENTS:
            q = q.where(ProductFeedback.sentiment == st)
            cq = cq.where(ProductFeedback.sentiment == st)

    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        cond = or_(
            Product.name.like(kw),
            Order.order_no.like(kw),
            Customer.display_name.like(kw),
            Customer.nickname.like(kw),
            Customer.customer_no.like(kw),
        )
        q = q.where(cond)
        cq = cq.where(cond)

    q = apply_date_range(q, ProductFeedback.created_at, date_from, date_to)
    cq = apply_date_range(cq, ProductFeedback.created_at, date_from, date_to)

    total = int((await db.execute(cq)).scalar() or 0)
    rows = (
        await db.execute(
            q.order_by(ProductFeedback.created_at.desc(), ProductFeedback.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items = []
    for fb, product_name, order_no, cust in rows:
        items.append(
            {
                "id": fb.id,
                "store_id": fb.store_id,
                "order_id": fb.order_id,
                "order_no": order_no,
                "product_id": fb.product_id,
                "product_name": product_name,
                "customer_id": fb.customer_id,
                "customer_name": (cust.display_name or cust.nickname) if cust else None,
                "customer_no": cust.customer_no if cust else None,
                "verdicts": list(fb.verdicts or []),
                "sentiment": fb.sentiment,
                "remark": fb.remark,
                "created_at": serialize_dt(fb.created_at),
            }
        )
    return success(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
            "verdict_codes": sorted(VERDICT_WHITELIST),
        },
        request=request,
    )

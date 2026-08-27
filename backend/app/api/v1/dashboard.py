from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.response import now_local, success
from app.models import Camera, Customer, Order, OrderItem, Payment, Product, VisitorEvent

router = APIRouter(prefix="/dashboard", tags=["数据统计"])


def _store_filter(user, col):
    if user.store_id and user.role.code != "SUPER_ADMIN":
        return col == user.store_id
    return None


@router.get("/overview")
async def dashboard_overview(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = Query(default=None, ge=1),
):
    today_start = now_local().replace(hour=0, minute=0, second=0, microsecond=0)
    effective_store = user.store_id if (user.store_id and user.role.code != "SUPER_ADMIN") else store_id

    order_q = select(func.count()).select_from(Order).where(Order.created_at >= today_start)
    revenue_q = select(func.coalesce(func.sum(Order.total_amount), 0)).where(
        Order.payment_status == "PAID", Order.paid_at >= today_start
    )
    paid_q = select(func.count()).select_from(Order).where(
        Order.payment_status == "PAID", Order.paid_at >= today_start
    )
    if effective_store:
        order_q = order_q.where(Order.store_id == effective_store)
        revenue_q = revenue_q.where(Order.store_id == effective_store)
        paid_q = paid_q.where(Order.store_id == effective_store)

    today_orders = await db.scalar(order_q) or 0
    today_revenue = await db.scalar(revenue_q) or 0

    visitor_q = select(func.count()).select_from(VisitorEvent).where(VisitorEvent.event_time >= today_start)
    if effective_store:
        cam_ids = (await db.execute(select(Camera.id).where(Camera.store_id == effective_store))).scalars().all()
        visitor_q = visitor_q.where(VisitorEvent.camera_id.in_(cam_ids or [-1]))
    today_visitors = await db.scalar(visitor_q) or 0

    today_new_members = await db.scalar(
        select(func.count()).select_from(Customer).where(Customer.created_at >= today_start)
    ) or 0

    paid_count = await db.scalar(paid_q) or 0
    avg_order = float(today_revenue) / paid_count if paid_count else 0

    # 热销：按门店订单明细聚合，不再用全库 sales_count 裸排
    hot_q = (
        select(Product.name, func.coalesce(func.sum(OrderItem.quantity), 0).label("sales"))
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.payment_status == "PAID", Order.paid_at >= today_start - timedelta(days=30))
        .group_by(Product.id, Product.name)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(10)
    )
    if effective_store:
        hot_q = hot_q.where(Order.store_id == effective_store)
    hot_products = await db.execute(hot_q)

    pay_base = select(Payment.provider, func.count()).where(
        Payment.status == "PAID", Payment.paid_at >= today_start
    )
    if effective_store:
        pay_base = pay_base.join(Order, Order.id == Payment.order_id).where(Order.store_id == effective_store)
    pay_base = pay_base.group_by(Payment.provider)
    pay_rows = {p: c for p, c in (await db.execute(pay_base)).all()}

    return success(
        {
            "store_id": effective_store,
            "today_orders": today_orders,
            "today_revenue": str(today_revenue),
            "today_visitors": today_visitors,
            "today_new_members": today_new_members,
            "avg_order_amount": round(avg_order, 2),
            "hot_products": [{"name": n, "sales": int(s)} for n, s in hot_products.all()],
            "payment_ratio": {"wechat": pay_rows.get("WECHAT", 0), "alipay": pay_rows.get("ALIPAY", 0)},
        },
        request=request,
    )

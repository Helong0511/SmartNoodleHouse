from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.response import now_local, success
from app.models import Customer, Order, Payment, Product, VisitorEvent

router = APIRouter(prefix="/dashboard", tags=["数据统计"])


@router.get("/overview")
async def dashboard_overview(request: Request, db: DbSession, user: CurrentUser):
    today_start = now_local().replace(hour=0, minute=0, second=0, microsecond=0)

    today_orders = await db.scalar(
        select(func.count()).select_from(Order).where(Order.created_at >= today_start)
    ) or 0

    today_revenue = await db.scalar(
        select(func.coalesce(func.sum(Order.total_amount), 0)).where(
            Order.payment_status == "PAID", Order.paid_at >= today_start
        )
    ) or 0

    today_visitors = await db.scalar(
        select(func.count()).select_from(VisitorEvent).where(VisitorEvent.event_time >= today_start)
    ) or 0

    today_new_members = await db.scalar(
        select(func.count()).select_from(Customer).where(Customer.created_at >= today_start)
    ) or 0

    paid_count = await db.scalar(
        select(func.count()).select_from(Order).where(Order.payment_status == "PAID", Order.paid_at >= today_start)
    ) or 0
    avg_order = float(today_revenue) / paid_count if paid_count else 0

    hot_products = await db.execute(
        select(Product.name, Product.sales_count).order_by(Product.sales_count.desc()).limit(10)
    )

    wechat_pay = await db.scalar(
        select(func.count()).select_from(Payment).where(Payment.provider == "WECHAT", Payment.status == "PAID", Payment.paid_at >= today_start)
    ) or 0
    alipay_pay = await db.scalar(
        select(func.count()).select_from(Payment).where(Payment.provider == "ALIPAY", Payment.status == "PAID", Payment.paid_at >= today_start)
    ) or 0

    return success(
        {
            "today_orders": today_orders,
            "today_revenue": str(today_revenue),
            "today_visitors": today_visitors,
            "today_new_members": today_new_members,
            "avg_order_amount": round(avg_order, 2),
            "hot_products": [{"name": n, "sales": s} for n, s in hot_products.all()],
            "payment_ratio": {"wechat": wechat_pay, "alipay": alipay_pay},
        },
        request=request,
    )

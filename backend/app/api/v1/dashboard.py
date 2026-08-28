"""数据概览：订单/净营收/退款/支付方式/外卖 KPI。"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.response import money, now_local, success
from app.models import (
    Camera,
    Customer,
    DeliveryOrder,
    DeliveryRefund,
    Order,
    OrderItem,
    Payment,
    PaymentRefund,
    Product,
    VisitorEvent,
)
from app.services.revenue_stats import member_discount_since, net_revenue_since, paid_order_count_since
from app.services.store_scope import require_bound_store

router = APIRouter(prefix="/dashboard", tags=["数据统计"])

# 当日「发生过成功支付」的状态（含后续部分/全额退款，否则退款后笔数会消失）
_PAID_LIKE = ("PAID", "PARTIAL_REFUND", "REFUNDED")


@router.get("/overview", dependencies=[require_permission("order:read")])
async def dashboard_overview(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = Query(default=None, ge=1),
):
    today_start = now_local().replace(hour=0, minute=0, second=0, microsecond=0)
    effective_store = require_bound_store(user, store_id)

    # ---------- 今日订单（按下单时间，含未支付）----------
    order_q = select(func.count()).select_from(Order).where(Order.created_at >= today_start)
    if effective_store:
        order_q = order_q.where(Order.store_id == effective_store)
    today_orders = int(await db.scalar(order_q) or 0)

    # ---------- 净营收 / 退款（支付入账 − 退款成功，含退昨日单）----------
    today_revenue = await net_revenue_since(db, since=today_start, store_id=effective_store)

    refund_q = select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
        PaymentRefund.status == "SUCCESS",
        func.coalesce(PaymentRefund.succeeded_at, PaymentRefund.created_at) >= today_start,
    )
    if effective_store is not None:
        refund_q = refund_q.join(Order, Order.id == PaymentRefund.order_id).where(
            Order.store_id == effective_store
        )
    today_refunded = money(Decimal(str(await db.scalar(refund_q) or 0)))

    gross_pay_q = select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.status.in_(_PAID_LIKE),
        Payment.paid_at >= today_start,
    )
    if effective_store is not None:
        gross_pay_q = gross_pay_q.join(Order, Order.id == Payment.order_id).where(
            Order.store_id == effective_store
        )
    today_gross = money(Decimal(str(await db.scalar(gross_pay_q) or 0)))

    # ---------- 客流 / 新会员 ----------
    visitor_q = select(func.count()).select_from(VisitorEvent).where(VisitorEvent.event_time >= today_start)
    if effective_store:
        cam_ids = list(
            (await db.execute(select(Camera.id).where(Camera.store_id == effective_store))).scalars().all()
        )
        visitor_q = visitor_q.where(VisitorEvent.camera_id.in_(cam_ids or [-1]))
    today_visitors = int(await db.scalar(visitor_q) or 0)

    today_new_members = int(
        await db.scalar(select(func.count()).select_from(Customer).where(Customer.created_at >= today_start))
        or 0
    )

    # ---------- 客单价：有效已付订单（未全额退）----------
    paid_count = await paid_order_count_since(db, since=today_start, store_id=effective_store)
    avg_order = round(float(today_revenue) / paid_count, 2) if paid_count else 0.0
    today_member_discount = await member_discount_since(db, since=today_start, store_id=effective_store)

    # ---------- 热销：近 30 天有效已付（排除全额退款）----------
    hot_q = (
        select(Product.name, func.coalesce(func.sum(OrderItem.quantity), 0).label("sales"))
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.payment_status.in_(("PAID", "PARTIAL_REFUND")),
            Order.paid_at >= today_start - timedelta(days=30),
        )
        .group_by(Product.id, Product.name)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(10)
    )
    if effective_store:
        hot_q = hot_q.where(Order.store_id == effective_store)
    hot_products = [
        {"name": n, "sales": int(s)} for n, s in (await db.execute(hot_q)).all()
    ]

    # ---------- 支付方式：按「今日成功支付笔数/金额」统计（含已退款笔，避免退款后归零）----------
    pay_stat_q = (
        select(
            Payment.provider,
            func.count().label("cnt"),
            func.coalesce(func.sum(Payment.amount), 0).label("gross"),
        )
        .where(Payment.status.in_(_PAID_LIKE), Payment.paid_at >= today_start)
        .group_by(Payment.provider)
    )
    if effective_store is not None:
        pay_stat_q = pay_stat_q.join(Order, Order.id == Payment.order_id).where(
            Order.store_id == effective_store
        )
    pay_rows = {str(p): (int(c), Decimal(str(g))) for p, c, g in (await db.execute(pay_stat_q)).all()}

    # 各渠道今日退款成功金额（按支付渠道归集）
    refund_by_provider_q = (
        select(
            Payment.provider,
            func.coalesce(func.sum(PaymentRefund.amount), 0).label("refunded"),
        )
        .join(Payment, Payment.id == PaymentRefund.payment_id)
        .where(
            PaymentRefund.status == "SUCCESS",
            func.coalesce(PaymentRefund.succeeded_at, PaymentRefund.created_at) >= today_start,
        )
        .group_by(Payment.provider)
    )
    if effective_store is not None:
        refund_by_provider_q = refund_by_provider_q.join(Order, Order.id == Payment.order_id).where(
            Order.store_id == effective_store
        )
    refund_by_provider = {
        str(p): Decimal(str(a)) for p, a in (await db.execute(refund_by_provider_q)).all()
    }

    def _channel_stat(code: str) -> dict:
        cnt, gross = pay_rows.get(code, (0, Decimal("0")))
        refunded = refund_by_provider.get(code, Decimal("0"))
        net = money(gross - refunded)
        if net < 0:
            net = money(Decimal("0"))
        return {
            "count": cnt,
            "gross_amount": str(money(gross)),
            "refunded_amount": str(money(refunded)),
            "net_amount": str(net),
        }

    wechat_stat = _channel_stat("WECHAT")
    alipay_stat = _channel_stat("ALIPAY")

    # ---------- 外卖 ----------
    async def _del_count(extra=None):
        q = select(func.count()).select_from(DeliveryOrder).join(Order, Order.id == DeliveryOrder.order_id)
        if effective_store:
            q = q.where(Order.store_id == effective_store)
        if extra is not None:
            q = q.where(extra)
        return int(await db.scalar(q) or 0)

    delivery_today = await _del_count(DeliveryOrder.created_at >= today_start)
    delivery_pending = await _del_count(DeliveryOrder.delivery_status == "PENDING")
    delivery_urging = await _del_count(
        (DeliveryOrder.urge_count > 0) & (DeliveryOrder.delivery_status.notin_(["DONE", "CANCELLED"]))
    )
    del_refund_q = (
        select(func.count())
        .select_from(DeliveryRefund)
        .join(Order, Order.id == DeliveryRefund.order_id)
        .where(DeliveryRefund.status == "PENDING")
    )
    if effective_store:
        del_refund_q = del_refund_q.where(Order.store_id == effective_store)
    delivery_refund_pending = int(await db.scalar(del_refund_q) or 0)
    delivery_revenue = await net_revenue_since(
        db, since=today_start, store_id=effective_store, channels=["MEITUAN", "ELEME"]
    )
    delivery_member_discount = await member_discount_since(
        db, since=today_start, store_id=effective_store, channels=["MEITUAN", "ELEME"]
    )

    return success(
        {
            "store_id": effective_store,
            "today_orders": today_orders,
            "today_gross_revenue": str(today_gross),
            "today_revenue": str(today_revenue),
            "today_refunded": str(today_refunded),
            "today_member_discount": str(today_member_discount),
            "today_visitors": today_visitors,
            "today_new_members": today_new_members,
            "avg_order_amount": avg_order,
            "paid_order_count": paid_count,
            "hot_products": hot_products,
            # 兼容旧前端字段（笔数）；同时给结构化明细
            "payment_ratio": {
                "wechat": wechat_stat["count"],
                "alipay": alipay_stat["count"],
                "wechat_detail": wechat_stat,
                "alipay_detail": alipay_stat,
            },
            "delivery": {
                "today_orders": delivery_today,
                "pending": delivery_pending,
                "urging": delivery_urging,
                "refund_pending": delivery_refund_pending,
                "today_revenue": str(delivery_revenue),
                "today_member_discount": str(delivery_member_discount),
            },
        },
        request=request,
    )

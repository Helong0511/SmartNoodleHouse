"""营收统计：实收 − 已退（含当日退昨日单）。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import money
from app.models import Order, Payment, PaymentRefund


async def net_revenue_since(
    db: AsyncSession,
    *,
    since: datetime,
    store_id: int | None = None,
    channels: list[str] | None = None,
) -> Decimal:
    """
    净营收 = 期间内支付成功金额 − 期间内退款成功金额。
    - 支付侧：Payment.status 曾为已支付（PAID/PARTIAL_REFUND/REFUNDED）且 paid_at >= since
    - 退款侧：PaymentRefund.status=SUCCESS 且 succeeded_at（或 created_at）>= since
    这样「昨日支付、今日退款」会正确冲减今日营收。
    """
    pay_q = select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.status.in_(("PAID", "PARTIAL_REFUND", "REFUNDED")),
        Payment.paid_at >= since,
    )
    if store_id is not None or channels:
        pay_q = pay_q.join(Order, Order.id == Payment.order_id)
        if store_id is not None:
            pay_q = pay_q.where(Order.store_id == store_id)
        if channels:
            pay_q = pay_q.where(Order.channel.in_(channels))

    refund_q = select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
        PaymentRefund.status == "SUCCESS",
        func.coalesce(PaymentRefund.succeeded_at, PaymentRefund.created_at) >= since,
    )
    if store_id is not None or channels:
        refund_q = refund_q.join(Order, Order.id == PaymentRefund.order_id)
        if store_id is not None:
            refund_q = refund_q.where(Order.store_id == store_id)
        if channels:
            refund_q = refund_q.where(Order.channel.in_(channels))

    gross = Decimal(str(await db.scalar(pay_q) or 0))
    refunded = Decimal(str(await db.scalar(refund_q) or 0))
    net = money(gross - refunded)
    return net if net > 0 else money(Decimal("0"))


async def paid_order_count_since(
    db: AsyncSession,
    *,
    since: datetime,
    store_id: int | None = None,
) -> int:
    """有效已付订单数：未全额退款（PAID / PARTIAL_REFUND）。"""
    q = select(func.count()).select_from(Order).where(
        Order.payment_status.in_(("PAID", "PARTIAL_REFUND")),
        Order.paid_at >= since,
    )
    if store_id is not None:
        q = q.where(Order.store_id == store_id)
    return int(await db.scalar(q) or 0)


async def member_discount_since(
    db: AsyncSession,
    *,
    since: datetime,
    store_id: int | None = None,
    channels: list[str] | None = None,
) -> Decimal:
    """期间内有效已付订单的会员优惠合计（discount_amount 之和）。"""
    q = select(func.coalesce(func.sum(Order.discount_amount), 0)).where(
        Order.payment_status.in_(("PAID", "PARTIAL_REFUND")),
        Order.paid_at >= since,
        Order.discount_amount > 0,
    )
    if store_id is not None:
        q = q.where(Order.store_id == store_id)
    if channels:
        q = q.where(Order.channel.in_(channels))
    return money(Decimal(str(await db.scalar(q) or 0)))


async def new_store_customers_since(
    db: AsyncSession,
    *,
    since: datetime,
    store_id: int,
) -> int:
    """门店维度：期间内首次在本店下单的顾客数（去重）。"""
    first_order_sq = (
        select(
            Order.customer_id.label("customer_id"),
            func.min(Order.created_at).label("first_at"),
        )
        .where(Order.store_id == store_id, Order.customer_id.isnot(None))
        .group_by(Order.customer_id)
        .subquery()
    )
    q = select(func.count()).select_from(first_order_sq).where(first_order_sq.c.first_at >= since)
    return int(await db.scalar(q) or 0)


async def store_dashboard_breakdown(db: AsyncSession, *, since: datetime) -> list[dict]:
    """多门店概览：按店汇总今日核心指标。"""
    from app.models import DeliveryOrder, Store

    stores = list(
        (await db.execute(select(Store).where(Store.status == 1).order_by(Store.store_no, Store.id))).scalars().all()
    )
    if not stores:
        return []

    order_rows = dict(
        (await db.execute(
            select(Order.store_id, func.count())
            .where(Order.created_at >= since)
            .group_by(Order.store_id)
        )).all()
    )

    gross_rows = dict(
        (await db.execute(
            select(Order.store_id, func.coalesce(func.sum(Payment.amount), 0))
            .join(Order, Order.id == Payment.order_id)
            .where(Payment.status.in_(("PAID", "PARTIAL_REFUND", "REFUNDED")), Payment.paid_at >= since)
            .group_by(Order.store_id)
        )).all()
    )

    refund_rows = dict(
        (await db.execute(
            select(Order.store_id, func.coalesce(func.sum(PaymentRefund.amount), 0))
            .join(Order, Order.id == PaymentRefund.order_id)
            .where(
                PaymentRefund.status == "SUCCESS",
                func.coalesce(PaymentRefund.succeeded_at, PaymentRefund.created_at) >= since,
            )
            .group_by(Order.store_id)
        )).all()
    )

    paid_rows = dict(
        (await db.execute(
            select(Order.store_id, func.count())
            .where(
                Order.payment_status.in_(("PAID", "PARTIAL_REFUND")),
                Order.paid_at >= since,
            )
            .group_by(Order.store_id)
        )).all()
    )

    discount_rows = dict(
        (await db.execute(
            select(Order.store_id, func.coalesce(func.sum(Order.discount_amount), 0))
            .where(
                Order.payment_status.in_(("PAID", "PARTIAL_REFUND")),
                Order.paid_at >= since,
                Order.discount_amount > 0,
            )
            .group_by(Order.store_id)
        )).all()
    )

    delivery_pending_rows = dict(
        (await db.execute(
            select(Order.store_id, func.count())
            .select_from(DeliveryOrder)
            .join(Order, Order.id == DeliveryOrder.order_id)
            .where(DeliveryOrder.delivery_status == "PENDING")
            .group_by(Order.store_id)
        )).all()
    )

    first_order_sq = (
        select(
            Order.store_id.label("store_id"),
            Order.customer_id.label("customer_id"),
            func.min(Order.created_at).label("first_at"),
        )
        .where(Order.customer_id.isnot(None))
        .group_by(Order.store_id, Order.customer_id)
        .subquery()
    )
    new_customer_rows = dict(
        (await db.execute(
            select(first_order_sq.c.store_id, func.count())
            .where(first_order_sq.c.first_at >= since)
            .group_by(first_order_sq.c.store_id)
        )).all()
    )

    items: list[dict] = []
    for store in stores:
        sid = int(store.id)
        gross = money(Decimal(str(gross_rows.get(sid, 0))))
        refunded = money(Decimal(str(refund_rows.get(sid, 0))))
        net = money(gross - refunded)
        if net < 0:
            net = money(Decimal("0"))
        paid = int(paid_rows.get(sid, 0))
        items.append(
            {
                "store_id": sid,
                "store_no": store.store_no,
                "store_name": store.name,
                "today_orders": int(order_rows.get(sid, 0)),
                "today_gross_revenue": str(gross),
                "today_revenue": str(net),
                "today_refunded": str(refunded),
                "today_member_discount": str(money(Decimal(str(discount_rows.get(sid, 0))))),
                "paid_order_count": paid,
                "avg_order_amount": round(float(net) / paid, 2) if paid else 0.0,
                "new_customers": int(new_customer_rows.get(sid, 0)),
                "delivery_pending": int(delivery_pending_rows.get(sid, 0)),
            }
        )
    return items

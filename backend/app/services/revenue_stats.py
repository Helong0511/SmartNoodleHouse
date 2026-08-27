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

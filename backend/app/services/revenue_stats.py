"""营收统计：实收 − 已退（含当日退昨日单）。"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import money, now_local
from app.models import Customer, Order, Payment, PaymentRefund, Product, Store

PeriodKey = Literal["today", "yesterday", "month"]

_PAID_ORDER = ("PAID", "PARTIAL_REFUND")
_PAID_PAYMENT = ("PAID", "PARTIAL_REFUND", "REFUNDED")


def resolve_period(period: PeriodKey) -> tuple[datetime, datetime, str]:
    """返回 (since, until, label)。"""
    now = now_local()
    until = now
    if period == "yesterday":
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        since = end - timedelta(days=1)
        return since, end, "昨日"
    if period == "month":
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return since, until, "本月"
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return since, until, "今日"


def _paid_order_filter(q, since: datetime, until: datetime | None = None):
    q = q.where(Order.payment_status.in_(_PAID_ORDER), Order.paid_at >= since)
    if until is not None:
        q = q.where(Order.paid_at < until)
    return q


def _order_created_filter(q, since: datetime, until: datetime | None = None):
    q = q.where(Order.created_at >= since)
    if until is not None:
        q = q.where(Order.created_at < until)
    return q


async def net_revenue_since(
    db: AsyncSession,
    *,
    since: datetime,
    store_id: int | None = None,
    channels: list[str] | None = None,
    prefer_ledger: bool = True,
) -> Decimal:
    """
    净营收 = 期间内支付成功金额 − 期间内退款成功金额。
    - 支付侧：Payment.status 曾为已支付（PAID/PARTIAL_REFUND/REFUNDED）且 paid_at >= since
    - 退款侧：PaymentRefund.status=SUCCESS 且 succeeded_at（或 created_at）>= since
    这样「昨日支付、今日退款」会正确冲减今日营收。

    若 prefer_ledger 且 financial_ledger 在窗口内有分录，则改从账本汇总
    （SALE − REFUND），与统一入账路径一致；允许负净收入。
    """
    if prefer_ledger and await _ledger_has_entries_since(db, since=since, store_id=store_id):
        return await net_revenue_from_ledger_since(
            db, since=since, until=None, store_id=store_id, channels=channels
        )

    pay_q = select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.status.in_(_PAID_PAYMENT),
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
    return net


async def _ledger_has_entries_since(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
) -> bool:
    from app.models import FinancialLedger

    q = select(func.count()).select_from(FinancialLedger).where(
        FinancialLedger.entry_type.in_(("SALE", "REFUND")),
        FinancialLedger.created_at >= since,
    )
    if until is not None:
        q = q.where(FinancialLedger.created_at < until)
    if store_id is not None:
        q = q.where(FinancialLedger.store_id == store_id)
    return int(await db.scalar(q) or 0) > 0


async def ledger_gross_since(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
    channels: list[str] | None = None,
) -> Decimal:
    """从 financial_ledger 汇总 SALE 金额。"""
    from app.models import FinancialLedger

    q = select(func.coalesce(func.sum(FinancialLedger.amount), 0)).where(
        FinancialLedger.entry_type == "SALE",
        FinancialLedger.created_at >= since,
    )
    if until is not None:
        q = q.where(FinancialLedger.created_at < until)
    if store_id is not None:
        q = q.where(FinancialLedger.store_id == store_id)
    if channels:
        q = q.where(FinancialLedger.channel.in_(channels))
    return money(Decimal(str(await db.scalar(q) or 0)))


async def ledger_refunded_since(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
    channels: list[str] | None = None,
) -> Decimal:
    """从 financial_ledger 汇总 REFUND 金额。"""
    from app.models import FinancialLedger

    q = select(func.coalesce(func.sum(FinancialLedger.amount), 0)).where(
        FinancialLedger.entry_type == "REFUND",
        FinancialLedger.created_at >= since,
    )
    if until is not None:
        q = q.where(FinancialLedger.created_at < until)
    if store_id is not None:
        q = q.where(FinancialLedger.store_id == store_id)
    if channels:
        q = q.where(FinancialLedger.channel.in_(channels))
    return money(Decimal(str(await db.scalar(q) or 0)))


async def net_revenue_from_ledger_since(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
    channels: list[str] | None = None,
) -> Decimal:
    """账本净收入 = SALE − REFUND（允许为负）。"""
    gross = await ledger_gross_since(
        db, since=since, until=until, store_id=store_id, channels=channels
    )
    refunded = await ledger_refunded_since(
        db, since=since, until=until, store_id=store_id, channels=channels
    )
    return money(gross - refunded)


async def paid_order_count_since(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
) -> int:
    """有效已付订单数：未全额退款（PAID / PARTIAL_REFUND）。"""
    q = select(func.count()).select_from(Order).where(
        Order.payment_status.in_(_PAID_ORDER),
        Order.paid_at >= since,
    )
    if until is not None:
        q = q.where(Order.paid_at < until)
    if store_id is not None:
        q = q.where(Order.store_id == store_id)
    return int(await db.scalar(q) or 0)


async def member_discount_since(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
    channels: list[str] | None = None,
) -> Decimal:
    """期间内有效已付订单的会员优惠合计（discount_amount 之和）。"""
    q = select(func.coalesce(func.sum(Order.discount_amount), 0)).where(
        Order.payment_status.in_(_PAID_ORDER),
        Order.paid_at >= since,
        Order.discount_amount > 0,
    )
    if until is not None:
        q = q.where(Order.paid_at < until)
    if store_id is not None:
        q = q.where(Order.store_id == store_id)
    if channels:
        q = q.where(Order.channel.in_(channels))
    return money(Decimal(str(await db.scalar(q) or 0)))


async def new_store_customers_since(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
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
    if until is not None:
        q = q.where(first_order_sq.c.first_at < until)
    return int(await db.scalar(q) or 0)


async def gross_revenue_since(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
    channels: list[str] | None = None,
) -> Decimal:
    pay_q = select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.status.in_(_PAID_PAYMENT),
        Payment.paid_at >= since,
    )
    if until is not None:
        pay_q = pay_q.where(Payment.paid_at < until)
    if store_id is not None or channels:
        pay_q = pay_q.join(Order, Order.id == Payment.order_id)
        if store_id is not None:
            pay_q = pay_q.where(Order.store_id == store_id)
        if channels:
            pay_q = pay_q.where(Order.channel.in_(channels))
    return money(Decimal(str(await db.scalar(pay_q) or 0)))


async def refunded_since(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
    channels: list[str] | None = None,
) -> Decimal:
    refund_q = select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
        PaymentRefund.status == "SUCCESS",
        func.coalesce(PaymentRefund.succeeded_at, PaymentRefund.created_at) >= since,
    )
    if until is not None:
        refund_q = refund_q.where(
            func.coalesce(PaymentRefund.succeeded_at, PaymentRefund.created_at) < until
        )
    if store_id is not None or channels:
        refund_q = refund_q.join(Order, Order.id == PaymentRefund.order_id)
        if store_id is not None:
            refund_q = refund_q.where(Order.store_id == store_id)
        if channels:
            refund_q = refund_q.where(Order.channel.in_(channels))
    return money(Decimal(str(await db.scalar(refund_q) or 0)))


async def order_amount_stats_since(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
    channels: list[str] | None = None,
) -> dict[str, Decimal]:
    """有效已付订单：商品小计、实付、会员/券/代金券优惠。"""
    q = select(
        func.coalesce(func.sum(Order.subtotal), 0),
        func.coalesce(func.sum(Order.discount_amount), 0),
        func.coalesce(func.sum(Order.coupon_discount), 0),
        func.coalesce(func.sum(Order.voucher_discount), 0),
        func.coalesce(func.sum(Order.total_amount), 0),
        func.coalesce(func.sum(Order.packing_fee), 0),
    ).where(Order.payment_status.in_(_PAID_ORDER), Order.paid_at >= since)
    if until is not None:
        q = q.where(Order.paid_at < until)
    if store_id is not None:
        q = q.where(Order.store_id == store_id)
    if channels:
        q = q.where(Order.channel.in_(channels))
    row = (await db.execute(q)).one()
    subtotal, member, coupon, voucher, payable, packing = (
        money(Decimal(str(v))) for v in row
    )
    total_discount = money(member + coupon + voucher)
    return {
        "subtotal": subtotal,
        "member_discount": member,
        "coupon_discount": coupon,
        "voucher_discount": voucher,
        "total_discount": total_discount,
        "payable_amount": payable,
        "packing_fee": packing,
    }


async def placed_order_count_since(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
) -> int:
    q = select(func.count()).select_from(Order)
    q = _order_created_filter(q, since, until)
    if store_id is not None:
        q = q.where(Order.store_id == store_id)
    return int(await db.scalar(q) or 0)


async def registered_member_count(
    db: AsyncSession,
    *,
    store_id: int | None = None,
) -> int:
    """注册会员总数（已挂等级或完善资料）。"""
    q = select(func.count()).select_from(Customer).where(
        Customer.status == 1,
        or_(
            Customer.member_level_id.isnot(None),
            Customer.phone.isnot(None),
            Customer.nickname.isnot(None),
            Customer.display_name.isnot(None),
        ),
    )
    if store_id is not None:
        q = q.where(
            Customer.id.in_(
                select(Order.customer_id)
                .where(Order.store_id == store_id, Order.customer_id.isnot(None))
                .distinct()
            )
        )
    return int(await db.scalar(q) or 0)


async def new_registered_members_since(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
) -> int:
    q = select(func.count()).select_from(Customer).where(Customer.created_at >= since)
    if until is not None:
        q = q.where(Customer.created_at < until)
    if store_id is not None:
        q = q.where(
            Customer.id.in_(
                select(Order.customer_id)
                .where(Order.store_id == store_id, Order.customer_id.isnot(None))
                .distinct()
            )
        )
    return int(await db.scalar(q) or 0)


async def product_sales_since(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
    limit: int = 20,
    by_store: bool = False,
) -> list[dict]:
    from app.models import OrderItem

    cols = [Product.name, func.coalesce(func.sum(OrderItem.quantity), 0).label("sales")]
    group = [Product.id, Product.name]
    if by_store:
        cols = [Store.name.label("store_name"), Product.name, func.coalesce(func.sum(OrderItem.quantity), 0).label("sales")]
        group = [Store.id, Store.name, Product.id, Product.name]

    hot_q = (
        select(*cols)
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.payment_status.in_(_PAID_ORDER), Order.paid_at >= since)
    )
    if until is not None:
        hot_q = hot_q.where(Order.paid_at < until)
    if by_store:
        hot_q = hot_q.join(Store, Store.id == Order.store_id)
    if store_id is not None:
        hot_q = hot_q.where(Order.store_id == store_id)
    hot_q = hot_q.group_by(*group).order_by(func.sum(OrderItem.quantity).desc()).limit(limit)

    rows = (await db.execute(hot_q)).all()
    if by_store:
        return [{"store_name": sn, "name": n, "sales": int(s)} for sn, n, s in rows]
    return [{"name": n, "sales": int(s)} for n, s in rows]


def _money_dict(d: dict[str, Decimal]) -> dict[str, str]:
    return {k: str(money(v)) for k, v in d.items()}


async def build_period_stats(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None,
    period_label: str,
    store_id: int | None,
) -> dict:
    """单周期汇总（今日/本月/昨日）。

    净收入优先 financial_ledger（有分录时）；否则回退 Payment/PaymentRefund。
    负净收入允许，不做 max(0, …) 钳制。
    """
    placed = await placed_order_count_since(db, since=since, until=until, store_id=store_id)
    paid_count = await paid_order_count_since(db, since=since, until=until, store_id=store_id)

    use_ledger = await _ledger_has_entries_since(db, since=since, until=until, store_id=store_id)
    if use_ledger:
        gross = await ledger_gross_since(db, since=since, until=until, store_id=store_id)
        refunded = await ledger_refunded_since(db, since=since, until=until, store_id=store_id)
        net = money(gross - refunded)
    else:
        gross = await gross_revenue_since(db, since=since, until=until, store_id=store_id)
        refunded = await refunded_since(db, since=since, until=until, store_id=store_id)
        net = money(gross - refunded)
    amounts = await order_amount_stats_since(db, since=since, until=until, store_id=store_id)
    avg_order = round(float(net) / paid_count, 2) if paid_count else 0.0

    new_members = await new_registered_members_since(
        db, since=since, until=until, store_id=store_id
    )
    new_store_customers = 0
    if store_id is not None:
        new_store_customers = await new_store_customers_since(
            db, since=since, until=until, store_id=store_id
        )

    return {
        "period_label": period_label,
        "since": since.isoformat(),
        "until": (until or now_local()).isoformat(),
        "order_count": placed,
        "paid_order_count": paid_count,
        "gross_revenue": str(gross),
        "net_revenue": str(net),
        "refunded_amount": str(refunded),
        "revenue_source": "financial_ledger" if use_ledger else "payments",
        "avg_order_amount": avg_order,
        "amounts": _money_dict(amounts),
        "new_registered_members": new_members,
        "new_store_customers": new_store_customers,
    }


async def store_period_breakdown(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
) -> list[dict]:
    """多门店：按店汇总指定周期指标。"""
    from app.models import DeliveryOrder

    stores = list(
        (await db.execute(select(Store).where(Store.status == 1).order_by(Store.store_no, Store.id))).scalars().all()
    )
    if not stores:
        return []

    order_q = select(Order.store_id, func.count()).group_by(Order.store_id)
    order_rows = dict((await db.execute(_order_created_filter(order_q, since, until))).all())

    gross_q = (
        select(Order.store_id, func.coalesce(func.sum(Payment.amount), 0))
        .join(Order, Order.id == Payment.order_id)
        .where(Payment.status.in_(_PAID_PAYMENT), Payment.paid_at >= since)
        .group_by(Order.store_id)
    )
    if until is not None:
        gross_q = gross_q.where(Payment.paid_at < until)
    gross_rows = dict((await db.execute(gross_q)).all())

    refund_q = (
        select(Order.store_id, func.coalesce(func.sum(PaymentRefund.amount), 0))
        .join(Order, Order.id == PaymentRefund.order_id)
        .where(
            PaymentRefund.status == "SUCCESS",
            func.coalesce(PaymentRefund.succeeded_at, PaymentRefund.created_at) >= since,
        )
        .group_by(Order.store_id)
    )
    if until is not None:
        refund_q = refund_q.where(
            func.coalesce(PaymentRefund.succeeded_at, PaymentRefund.created_at) < until
        )
    refund_rows = dict((await db.execute(refund_q)).all())

    paid_q = (
        select(Order.store_id, func.count())
        .where(Order.payment_status.in_(_PAID_ORDER), Order.paid_at >= since)
        .group_by(Order.store_id)
    )
    if until is not None:
        paid_q = paid_q.where(Order.paid_at < until)
    paid_rows = dict((await db.execute(paid_q)).all())

    amount_q = (
        select(
            Order.store_id,
            func.coalesce(func.sum(Order.subtotal), 0),
            func.coalesce(func.sum(Order.discount_amount), 0),
            func.coalesce(func.sum(Order.coupon_discount), 0),
            func.coalesce(func.sum(Order.voucher_discount), 0),
            func.coalesce(func.sum(Order.total_amount), 0),
        )
        .where(Order.payment_status.in_(_PAID_ORDER), Order.paid_at >= since)
        .group_by(Order.store_id)
    )
    if until is not None:
        amount_q = amount_q.where(Order.paid_at < until)
    amount_rows = {
        int(sid): (sub, mem, coup, vou, pay)
        for sid, sub, mem, coup, vou, pay in (await db.execute(amount_q)).all()
    }

    delivery_pending_rows = dict(
        (
            await db.execute(
                select(Order.store_id, func.count())
                .select_from(DeliveryOrder)
                .join(Order, Order.id == DeliveryOrder.order_id)
                .where(DeliveryOrder.delivery_status == "PENDING")
                .group_by(Order.store_id)
            )
        ).all()
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
    new_cust_q = (
        select(first_order_sq.c.store_id, func.count())
        .where(first_order_sq.c.first_at >= since)
        .group_by(first_order_sq.c.store_id)
    )
    if until is not None:
        new_cust_q = new_cust_q.where(first_order_sq.c.first_at < until)
    new_customer_rows = dict((await db.execute(new_cust_q)).all())

    items: list[dict] = []
    for store in stores:
        sid = int(store.id)
        gross = money(Decimal(str(gross_rows.get(sid, 0))))
        refunded = money(Decimal(str(refund_rows.get(sid, 0))))
        net = money(gross - refunded)
        paid = int(paid_rows.get(sid, 0))
        sub, mem, coup, vou, pay = amount_rows.get(sid, (0, 0, 0, 0, 0))
        mem, coup, vou = money(Decimal(str(mem))), money(Decimal(str(coup))), money(Decimal(str(vou)))
        total_disc = money(mem + coup + vou)
        items.append(
            {
                "store_id": sid,
                "store_no": store.store_no,
                "store_name": store.name,
                "order_count": int(order_rows.get(sid, 0)),
                "paid_order_count": paid,
                "gross_revenue": str(gross),
                "net_revenue": str(net),
                "refunded_amount": str(refunded),
                "subtotal": str(money(Decimal(str(sub)))),
                "member_discount": str(mem),
                "coupon_discount": str(coup),
                "voucher_discount": str(vou),
                "total_discount": str(total_disc),
                "payable_amount": str(money(Decimal(str(pay)))),
                "avg_order_amount": round(float(net) / paid, 2) if paid else 0.0,
                "new_store_customers": int(new_customer_rows.get(sid, 0)),
                "delivery_pending": int(delivery_pending_rows.get(sid, 0)),
            }
        )
    return items


async def daily_series(
    db: AsyncSession,
    *,
    days: int = 7,
    store_id: int | None = None,
) -> list[dict]:
    """近 N 日每日净营收与订单数。"""
    days = max(1, min(days, 31))
    now = now_local()
    series: list[dict] = []
    for offset in range(days - 1, -1, -1):
        day_start = (now - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        placed = await placed_order_count_since(db, since=day_start, until=day_end, store_id=store_id)
        paid = await paid_order_count_since(db, since=day_start, until=day_end, store_id=store_id)
        gross = await gross_revenue_since(db, since=day_start, until=day_end, store_id=store_id)
        refunded = await refunded_since(db, since=day_start, until=day_end, store_id=store_id)
        net = money(gross - refunded)
        amounts = await order_amount_stats_since(db, since=day_start, until=day_end, store_id=store_id)
        series.append(
            {
                "date": day_start.strftime("%Y-%m-%d"),
                "weekday": day_start.strftime("%a"),
                "order_count": placed,
                "paid_order_count": paid,
                "gross_revenue": str(gross),
                "net_revenue": str(net),
                "refunded_amount": str(refunded),
                "total_discount": str(amounts["total_discount"]),
            }
        )
    return series


_PAYMENT_CHANNELS = (
    ("WECHAT", "微信"),
    ("ALIPAY", "支付宝"),
    ("CASH", "现金"),
    ("MIXED", "组合支付"),
)


async def payment_channel_stats(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
) -> list[dict]:
    pay_q = (
        select(
            Payment.provider,
            func.count().label("cnt"),
            func.coalesce(func.sum(Payment.amount), 0).label("gross"),
        )
        .where(Payment.status.in_(_PAID_PAYMENT), Payment.paid_at >= since)
        .group_by(Payment.provider)
    )
    if until is not None:
        pay_q = pay_q.where(Payment.paid_at < until)
    if store_id is not None:
        pay_q = pay_q.join(Order, Order.id == Payment.order_id).where(Order.store_id == store_id)
    pay_rows = {str(p): (int(c), Decimal(str(g))) for p, c, g in (await db.execute(pay_q)).all()}

    refund_q = (
        select(
            Payment.provider,
            func.coalesce(func.sum(PaymentRefund.amount), 0).label("refunded"),
        )
        .join(Payment, Payment.id == PaymentRefund.payment_id)
        .where(
            PaymentRefund.status == "SUCCESS",
            func.coalesce(PaymentRefund.succeeded_at, PaymentRefund.created_at) >= since,
        )
        .group_by(Payment.provider)
    )
    if until is not None:
        refund_q = refund_q.where(
            func.coalesce(PaymentRefund.succeeded_at, PaymentRefund.created_at) < until
        )
    if store_id is not None:
        refund_q = refund_q.join(Order, Order.id == PaymentRefund.order_id).where(
            Order.store_id == store_id
        )
    refund_rows = {str(p): Decimal(str(a)) for p, a in (await db.execute(refund_q)).all()}

    items: list[dict] = []
    for code, label in _PAYMENT_CHANNELS:
        cnt, gross = pay_rows.get(code, (0, Decimal("0")))
        refunded = refund_rows.get(code, Decimal("0"))
        net = money(gross - refunded)
        items.append(
            {
                "provider": code,
                "label": label,
                "count": cnt,
                "gross_amount": str(money(gross)),
                "refunded_amount": str(money(refunded)),
                "net_amount": str(net),
            }
        )
    return items


async def delivery_kpi(
    db: AsyncSession,
    *,
    since: datetime,
    until: datetime | None = None,
    store_id: int | None = None,
) -> dict:
    from app.models import DeliveryOrder, DeliveryRefund

    async def _count(extra=None) -> int:
        q = select(func.count()).select_from(DeliveryOrder).join(Order, Order.id == DeliveryOrder.order_id)
        if store_id:
            q = q.where(Order.store_id == store_id)
        if extra is not None:
            q = q.where(extra)
        return int(await db.scalar(q) or 0)

    async def _period_count(extra=None) -> int:
        q = (
            select(func.count())
            .select_from(DeliveryOrder)
            .join(Order, Order.id == DeliveryOrder.order_id)
            .where(DeliveryOrder.created_at >= since)
        )
        if until is not None:
            q = q.where(DeliveryOrder.created_at < until)
        if store_id:
            q = q.where(Order.store_id == store_id)
        if extra is not None:
            q = q.where(extra)
        return int(await db.scalar(q) or 0)

    async def _status_count(extra=None) -> int:
        """周期视图（有 until）按下单时间过滤；实时视图返回当前全量状态数。"""
        if until is not None:
            return await _period_count(extra)
        return await _count(extra)

    orders = await _period_count()

    # 外卖入账走 financial_ledger（无 Payment 行），KPI 必须读账本而非 Payment 汇总
    _channels = ["MEITUAN", "ELEME"]
    if await _ledger_has_entries_since(db, since=since, until=until, store_id=store_id):
        gross = await ledger_gross_since(
            db, since=since, until=until, store_id=store_id, channels=_channels
        )
        refunded = await ledger_refunded_since(
            db, since=since, until=until, store_id=store_id, channels=_channels
        )
    else:
        gross = await gross_revenue_since(
            db, since=since, until=until, store_id=store_id, channels=_channels
        )
        refunded = await refunded_since(
            db, since=since, until=until, store_id=store_id, channels=_channels
        )
    # 允许负净收入（退款大于收款），与 net_revenue_from_ledger_since 一致
    revenue = money(gross - refunded)

    member_discount = await member_discount_since(
        db, since=since, until=until, store_id=store_id, channels=["MEITUAN", "ELEME"]
    )

    del_refund_q = (
        select(func.count())
        .select_from(DeliveryRefund)
        .join(Order, Order.id == DeliveryRefund.order_id)
        .where(DeliveryRefund.status == "PENDING")
    )
    if store_id:
        del_refund_q = del_refund_q.where(Order.store_id == store_id)

    return {
        "orders": orders,
        "revenue": str(revenue),
        "member_discount": str(member_discount),
        "pending": await _status_count(DeliveryOrder.delivery_status == "PENDING"),
        "accepted": await _status_count(DeliveryOrder.delivery_status.in_(["ACCEPTED", "READY"])),
        "delivering": await _status_count(
            DeliveryOrder.delivery_status.notin_(["PENDING", "ACCEPTED", "READY", "DONE", "CANCELLED"])
        ),
        "urging": await _status_count(
            (DeliveryOrder.urge_count > 0)
            & (DeliveryOrder.delivery_status.notin_(["DONE", "CANCELLED"]))
        ),
        "refund_pending": int(await db.scalar(del_refund_q) or 0),
        "meituan_orders": await _period_count(DeliveryOrder.platform == "MEITUAN"),
        "eleme_orders": await _period_count(DeliveryOrder.platform == "ELEME"),
    }


async def visitor_count_since(
    db: AsyncSession,
    *,
    since: datetime,
    store_id: int | None = None,
) -> int:
    from app.models import Camera, VisitorEvent

    q = select(func.count()).select_from(VisitorEvent).where(VisitorEvent.event_time >= since)
    if store_id:
        cam_ids = list(
            (await db.execute(select(Camera.id).where(Camera.store_id == store_id))).scalars().all()
        )
        q = q.where(VisitorEvent.camera_id.in_(cam_ids or [-1]))
    return int(await db.scalar(q) or 0)

"""收银班次与日结统计。"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import ensure_local_aware, now_local
from app.models import CashierShift, DiningTable, Order, Payment, User

_PAID = "PAID"


def _money(v: Decimal | None) -> Decimal:
    if v is None:
        return Decimal("0.00")
    return Decimal(str(v)).quantize(Decimal("0.01"))


def _day_bounds(date_str: str | None) -> tuple[datetime, datetime]:
    now = now_local()
    if date_str:
        try:
            day = datetime.fromisoformat(date_str[:10])
        except ValueError:
            day = now
    else:
        day = now
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    if start.tzinfo is None:
        start = ensure_local_aware(start)
    end = start + timedelta(days=1)
    return start, end


async def _aggregate_payments(
    db: AsyncSession,
    *,
    operator_id: int | None,
    since: datetime,
    until: datetime | None = None,
) -> dict[str, Decimal]:
    q = select(Payment.provider, func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.status == _PAID,
        Payment.paid_at >= since,
    )
    if until is not None:
        q = q.where(Payment.paid_at < until)
    if operator_id is not None:
        q = q.where(Payment.operator_id == int(operator_id))
    rows = (await db.execute(q.group_by(Payment.provider))).all()
    cash = wechat = alipay = other = Decimal("0")
    for provider, total in rows:
        amt = _money(total)
        p = (provider or "").upper()
        if p == "CASH":
            cash += amt
        elif p == "WECHAT":
            wechat += amt
        elif p == "ALIPAY":
            alipay += amt
        else:
            other += amt
    total_amount = cash + wechat + alipay + other
    return {
        "total_cash": cash,
        "total_wechat": wechat,
        "total_alipay": alipay,
        "total_other": other,
        "total_amount": total_amount,
    }


class PosShiftService:
    @classmethod
    async def get_open_shift(cls, db: AsyncSession, operator_id: int) -> CashierShift | None:
        return (
            await db.execute(
                select(CashierShift)
                .where(CashierShift.operator_id == int(operator_id), CashierShift.status == "OPEN")
                .order_by(CashierShift.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    @classmethod
    async def open_shift(
        cls,
        db: AsyncSession,
        *,
        store_id: int,
        operator_id: int,
        opening_float: Decimal,
    ) -> CashierShift:
        existing = await cls.get_open_shift(db, operator_id)
        if existing:
            raise AppException(40013, "您已有进行中的班次，请先交班")
        shift = CashierShift(
            store_id=int(store_id),
            operator_id=int(operator_id),
            opening_float=_money(opening_float),
            status="OPEN",
            opened_at=now_local(),
        )
        db.add(shift)
        await db.flush()
        return shift

    @classmethod
    async def _fill_shift_totals(cls, db: AsyncSession, shift: CashierShift) -> dict[str, Decimal]:
        until = shift.closed_at or now_local()
        stats = await _aggregate_payments(
            db,
            operator_id=int(shift.operator_id),
            since=ensure_local_aware(shift.opened_at),
            until=until,
        )
        shift.total_cash = stats["total_cash"]
        shift.total_wechat = stats["total_wechat"]
        shift.total_alipay = stats["total_alipay"]
        shift.total_amount = stats["total_amount"]
        return stats

    @classmethod
    async def close_shift(
        cls,
        db: AsyncSession,
        shift: CashierShift,
        *,
        closing_cash: Decimal | None,
        remark: str | None,
        closed_by: int | None = None,
    ) -> CashierShift:
        if shift.status != "OPEN":
            raise AppException(40014, "班次已交班")
        stats = await cls._fill_shift_totals(db, shift)
        shift.status = "CLOSED"
        shift.closed_at = now_local()
        shift.remark = (remark or "").strip() or None
        shift.closed_by = closed_by
        if closing_cash is not None:
            shift.closing_cash = _money(closing_cash)
            expected_cash = _money(shift.opening_float) + stats["total_cash"]
            shift.cash_difference = _money(closing_cash) - expected_cash
        await db.flush()
        return shift

    @classmethod
    async def shift_payload(cls, db: AsyncSession, shift: CashierShift, *, live: bool = False) -> dict:
        if live and shift.status == "OPEN":
            stats = await cls._fill_shift_totals(db, shift)
        else:
            stats = {
                "total_cash": _money(shift.total_cash),
                "total_wechat": _money(shift.total_wechat),
                "total_alipay": _money(shift.total_alipay),
                "total_amount": _money(shift.total_amount),
            }
        op = await db.get(User, shift.operator_id)
        return {
            "id": shift.id,
            "store_id": shift.store_id,
            "operator_id": shift.operator_id,
            "operator_name": (op.display_name or op.username) if op else None,
            "opening_float": str(shift.opening_float),
            "status": shift.status,
            "opened_at": shift.opened_at.isoformat() if shift.opened_at else None,
            "closed_at": shift.closed_at.isoformat() if shift.closed_at else None,
            "total_cash": str(stats["total_cash"]),
            "total_wechat": str(stats["total_wechat"]),
            "total_alipay": str(stats["total_alipay"]),
            "total_amount": str(stats["total_amount"]),
            "closing_cash": str(shift.closing_cash) if shift.closing_cash is not None else None,
            "cash_difference": str(shift.cash_difference) if shift.cash_difference is not None else None,
            "remark": shift.remark,
            "closed_by": shift.closed_by,
        }

    @classmethod
    async def daily_settlement(
        cls,
        db: AsyncSession,
        *,
        store_id: int | None,
        date_str: str | None,
    ) -> dict:
        start, end = _day_bounds(date_str)
        q = select(CashierShift).where(CashierShift.opened_at >= start, CashierShift.opened_at < end)
        if store_id is not None:
            q = q.where(CashierShift.store_id == int(store_id))
        shifts = list((await db.execute(q.order_by(CashierShift.opened_at))).scalars().all())
        items = [await cls.shift_payload(db, s) for s in shifts]
        sum_cash = sum(Decimal(i["total_cash"]) for i in items)
        sum_wechat = sum(Decimal(i["total_wechat"]) for i in items)
        sum_alipay = sum(Decimal(i["total_alipay"]) for i in items)
        sum_total = sum(Decimal(i["total_amount"]) for i in items)
        sum_float = sum(Decimal(i["opening_float"]) for i in items)
        return {
            "date": start.date().isoformat(),
            "store_id": store_id,
            "shift_count": len(items),
            "total_opening_float": str(_money(sum_float)),
            "total_cash": str(_money(sum_cash)),
            "total_wechat": str(_money(sum_wechat)),
            "total_alipay": str(_money(sum_alipay)),
            "total_amount": str(_money(sum_total)),
            "shifts": items,
        }

    @classmethod
    async def my_today_summary(cls, db: AsyncSession, operator_id: int) -> dict:
        start, end = _day_bounds(None)
        stats = await _aggregate_payments(
            db,
            operator_id=int(operator_id),
            since=start,
            until=end,
        )
        shift = await cls.get_open_shift(db, operator_id)
        return {
            "date": start.date().isoformat(),
            "operator_id": operator_id,
            "opening_float": str(shift.opening_float) if shift else "0.00",
            "shift_status": shift.status if shift else None,
            "shift_id": shift.id if shift else None,
            "total_cash": str(stats["total_cash"]),
            "total_wechat": str(stats["total_wechat"]),
            "total_alipay": str(stats["total_alipay"]),
            "total_amount": str(stats["total_amount"]),
        }

    @classmethod
    async def list_shift_orders(
        cls,
        db: AsyncSession,
        shift: CashierShift,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """本班收银员收款订单（按支付记录 operator_id + 班次时段）。"""
        since = ensure_local_aware(shift.opened_at)
        until = ensure_local_aware(shift.closed_at) if shift.closed_at else now_local()
        page = max(1, int(page))
        page_size = min(max(1, int(page_size)), 100)

        paid_order_ids = (
            select(Payment.order_id)
            .where(
                Payment.operator_id == int(shift.operator_id),
                Payment.status == _PAID,
                Payment.paid_at >= since,
                Payment.paid_at < until,
            )
            .distinct()
        )
        base_q = select(Order).where(Order.id.in_(paid_order_ids), Order.channel == "POS")
        total = (await db.execute(select(func.count()).select_from(base_q.subquery()))).scalar() or 0
        orders = (
            await db.execute(
                base_q.order_by(Order.paid_at.desc(), Order.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()

        table_ids = [o.table_id for o in orders if o.table_id]
        tables: dict[int, DiningTable] = {}
        if table_ids:
            t_rows = (await db.execute(select(DiningTable).where(DiningTable.id.in_(table_ids)))).scalars().all()
            tables = {t.id: t for t in t_rows}

        from app.services.pos_refund_service import PosRefundService

        refund_map = await PosRefundService.latest_by_orders(db, [o.id for o in orders])

        items = []
        for o in orders:
            table = tables.get(o.table_id) if o.table_id else None
            rr = refund_map.get(o.id)
            items.append(
                {
                    "id": o.id,
                    "order_no": o.order_no,
                    "total_amount": str(o.total_amount),
                    "payment_method": o.payment_method,
                    "payment_status": o.payment_status,
                    "status": o.status,
                    "dining_type": o.dining_type,
                    "paid_at": o.paid_at.isoformat() if o.paid_at else None,
                    "ordered_at": o.ordered_at.isoformat() if o.ordered_at else None,
                    "table_no": table.table_no if table else None,
                    "table_name": table.table_name if table else None,
                    "remark": o.remark,
                    "refund_request": (
                        {
                            "id": rr.id,
                            "status": rr.status,
                            "amount": str(rr.amount),
                            "reason": rr.reason,
                            "fail_reason": rr.fail_reason,
                        }
                        if rr
                        else None
                    ),
                }
            )
        return {
            "shift_id": shift.id,
            "opened_at": shift.opened_at.isoformat() if shift.opened_at else None,
            "total": int(total),
            "page": page,
            "page_size": page_size,
            "items": items,
        }

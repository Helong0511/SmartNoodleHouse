"""财务账本只读查询（对账明细）。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.response import apply_datetime_range, calc_pages, money, success, serialize_dt
from app.models import FinancialLedger
from app.services.store_scope import require_bound_store

router = APIRouter(prefix="/financial-ledger", tags=["财务账本"])


@router.get("", dependencies=[require_permission("payment:read")])
async def list_financial_ledger(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    store_id: int | None = None,
    entry_type: str | None = Query(None, description="SALE/REFUND"),
    channel: str | None = None,
    order_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    sid = require_bound_store(user, store_id)
    q = select(FinancialLedger)
    cq = select(func.count()).select_from(FinancialLedger)
    if sid is not None:
        q = q.where(FinancialLedger.store_id == sid)
        cq = cq.where(FinancialLedger.store_id == sid)
    if entry_type:
        et = entry_type.strip().upper()
        q = q.where(FinancialLedger.entry_type == et)
        cq = cq.where(FinancialLedger.entry_type == et)
    if channel:
        ch = channel.strip().upper()
        q = q.where(FinancialLedger.channel == ch)
        cq = cq.where(FinancialLedger.channel == ch)
    if order_id:
        q = q.where(FinancialLedger.order_id == int(order_id))
        cq = cq.where(FinancialLedger.order_id == int(order_id))
    q = apply_datetime_range(q, FinancialLedger.created_at, date_from, date_to)
    cq = apply_datetime_range(cq, FinancialLedger.created_at, date_from, date_to)

    total = int((await db.execute(cq)).scalar() or 0)
    rows = (
        await db.execute(
            q.order_by(FinancialLedger.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    items = [
        {
            "id": r.id,
            "store_id": r.store_id,
            "entry_type": r.entry_type,
            "channel": r.channel,
            "provider": r.provider,
            "amount": str(money(r.amount)),
            "currency": r.currency,
            "order_id": r.order_id,
            "payment_id": r.payment_id,
            "refund_id": r.refund_id,
            "delivery_order_id": r.delivery_order_id,
            "delivery_refund_id": r.delivery_refund_id,
            "remark": r.remark,
            "created_at": serialize_dt(r.created_at),
        }
        for r in rows
    ]
    return success(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
        },
        request=request,
    )

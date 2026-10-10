"""到店排队：顾客取号/查号/取消 + 员工叫号/落座/取消。"""
from datetime import date

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentCustomer, CurrentUser, DbSession
from app.core.exceptions import AppException
from app.core.response import naive_local_now, now_local, success
from app.models import QueueTicket
from app.services.system_settings_service import SystemSettingsService
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/api", tags=["排队"])

ACTIVE_STATUSES = ("WAITING", "CALLED")


class QueueTakeBody(BaseModel):
    store_id: int = Field(..., gt=0)
    people_count: int = Field(..., ge=1, le=20)


def _queue_payload(ticket: QueueTicket, ahead_count: int) -> dict:
    return {
        "id": int(ticket.id),
        "ticket_no": ticket.ticket_no,
        "number": int(ticket.number),
        "people_count": int(ticket.people_count),
        "status": ticket.status,
        "ahead_count": int(ahead_count),
        "issued_at": ticket.issued_at.isoformat() if ticket.issued_at else None,
    }


async def _ahead_count(db, store_id: int, issue_date: date, number: int) -> int:
    """前方仍在等待的桌数（number 更小的 WAITING 票）。"""
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(QueueTicket)
                .where(
                    QueueTicket.store_id == store_id,
                    QueueTicket.issue_date == issue_date,
                    QueueTicket.status == "WAITING",
                    QueueTicket.number < number,
                )
            )
        ).scalar_one()
    )


async def _max_number(db, store_id: int, issue_date: date) -> int:
    row = (
        await db.execute(
            select(func.max(QueueTicket.number))
            .where(QueueTicket.store_id == store_id, QueueTicket.issue_date == issue_date)
            .with_for_update()
        )
    ).scalar_one()
    return int(row or 0)


async def _queue_enabled(db) -> bool:
    raw = (await SystemSettingsService.get_value(db, "queue_enabled", "0")).strip()
    print(f"[DBG] _queue_enabled db={id(db)} raw={raw!r} cache={SystemSettingsService._cache}", flush=True)  # TODO 临时调试
    return raw == "1"


@router.post("/miniprogram/queue/take")
async def take_queue_ticket(body: QueueTakeBody, request: Request, db: DbSession, customer: CurrentCustomer):
    """排队取号：开关拦截；同店当日已有有效票则幂等返回；当日门店号递增防重。"""
    if not await _queue_enabled(db):
        raise AppException(40020, "排队功能未开启")
    today = now_local().date()
    existing = (
        (
            await db.execute(
                select(QueueTicket)
                .where(
                    QueueTicket.store_id == body.store_id,
                    QueueTicket.customer_id == customer.id,
                    QueueTicket.issue_date == today,
                    QueueTicket.status.in_(ACTIVE_STATUSES),
                )
                .order_by(QueueTicket.id.desc())
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        ahead = await _ahead_count(db, existing.store_id, today, existing.number)
        return success(_queue_payload(existing, ahead), request=request)

    ticket: QueueTicket | None = None
    for attempt in (1, 2):
        number = await _max_number(db, body.store_id, today) + 1
        candidate = QueueTicket(
            store_id=body.store_id,
            customer_id=customer.id,
            ticket_no=f"A{number:03d}",
            number=number,
            issue_date=today,
            people_count=body.people_count,
            status="WAITING",
            issued_at=naive_local_now(),
        )
        db.add(candidate)
        try:
            await db.flush()
            ticket = candidate
            break
        except IntegrityError:
            # UNIQUE(store_id, issue_date, number) 并发兜底：回滚后重算一次
            await db.rollback()
            if attempt == 2:
                raise AppException(40021, "取号繁忙，请重试")
    if ticket is None:  # pragma: no cover - 上方已抛
        raise AppException(40021, "取号繁忙，请重试")
    ahead = await _ahead_count(db, ticket.store_id, today, ticket.number)
    return success(_queue_payload(ticket, ahead), request=request)


@router.get("/miniprogram/queue/current")
async def my_current_ticket(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    store_id: int,
):
    """我在指定门店当日的小票；无有效票返回 null。"""
    today = now_local().date()
    ticket = (
        (
            await db.execute(
                select(QueueTicket)
                .where(
                    QueueTicket.store_id == store_id,
                    QueueTicket.customer_id == customer.id,
                    QueueTicket.issue_date == today,
                    QueueTicket.status.in_(ACTIVE_STATUSES),
                )
                .order_by(QueueTicket.id.desc())
            )
        )
        .scalars()
        .first()
    )
    if ticket is None:
        return success(None, request=request)
    ahead = await _ahead_count(db, ticket.store_id, today, ticket.number)
    return success(_queue_payload(ticket, ahead), request=request)


@router.post("/miniprogram/queue/{ticket_id}/cancel")
async def cancel_my_ticket(ticket_id: int, request: Request, db: DbSession, customer: CurrentCustomer):
    """顾客取消本人等待中的排队号。"""
    ticket = await db.get(QueueTicket, ticket_id)
    if not ticket or int(ticket.customer_id or 0) != int(customer.id):
        raise AppException(40404, "排队号不存在")
    if ticket.status != "WAITING":
        raise AppException(40021, "当前状态不可取消")
    ticket.status = "CANCELLED"
    ticket.cancelled_at = naive_local_now()
    await db.flush()
    return success({"ticket_no": ticket.ticket_no, "status": ticket.status}, request=request)


async def _get_queue_ticket(db, ticket_id: int) -> QueueTicket:
    ticket = await db.get(QueueTicket, ticket_id)
    if not ticket:
        raise AppException(40404, "排队号不存在")
    return ticket


@router.get("/queue/admin/today")
async def admin_today_tickets(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int,
    status: str | None = None,
):
    """当日某门店全部排队号：WAITING/CALLED 在前按 number 正序。"""
    today = now_local().date()
    stmt = select(QueueTicket).where(
        QueueTicket.store_id == store_id,
        QueueTicket.issue_date == today,
    )
    if status:
        stmt = stmt.where(QueueTicket.status == status.strip().upper())
    stmt = stmt.order_by(
        case((QueueTicket.status.in_(ACTIVE_STATUSES), 0), else_=1),
        QueueTicket.number.asc(),
        QueueTicket.id.asc(),
    )
    rows = (await db.execute(stmt)).scalars().all()
    items = [
        {
            "id": int(t.id),
            "ticket_no": t.ticket_no,
            "number": int(t.number),
            "people_count": int(t.people_count),
            "status": t.status,
            "customer_id": int(t.customer_id) if t.customer_id else None,
            "issued_at": t.issued_at.isoformat() if t.issued_at else None,
            "called_at": t.called_at.isoformat() if t.called_at else None,
            "seated_at": t.seated_at.isoformat() if t.seated_at else None,
            "cancelled_at": t.cancelled_at.isoformat() if t.cancelled_at else None,
        }
        for t in rows
    ]
    return success(items, request=request)


@router.post("/queue/admin/{ticket_id}/call")
async def admin_call_ticket(ticket_id: int, request: Request, db: DbSession, user: CurrentUser):
    """叫号：WAITING→CALLED，并广播到叫号屏 display 渠道。"""
    ticket = await _get_queue_ticket(db, ticket_id)
    if ticket.status != "WAITING":
        raise AppException(40021, "仅等待中的排队号可叫号")
    ticket.status = "CALLED"
    ticket.called_at = naive_local_now()
    await db.flush()
    await ws_manager.broadcast(
        "display",
        "QUEUE_CALLED",
        {
            "ticket_no": ticket.ticket_no,
            "store_id": int(ticket.store_id),
            "number": int(ticket.number),
        },
        store_id=int(ticket.store_id),
    )
    return success(
        {"ticket_no": ticket.ticket_no, "number": int(ticket.number), "status": ticket.status},
        request=request,
    )


@router.post("/queue/admin/{ticket_id}/seat")
async def admin_seat_ticket(ticket_id: int, request: Request, db: DbSession, user: CurrentUser):
    """落座：CALLED→SEATED。"""
    ticket = await _get_queue_ticket(db, ticket_id)
    if ticket.status != "CALLED":
        raise AppException(40021, "仅已叫号的排队号可标记落座")
    ticket.status = "SEATED"
    ticket.seated_at = naive_local_now()
    await db.flush()
    return success({"ticket_no": ticket.ticket_no, "status": ticket.status}, request=request)


@router.post("/queue/admin/{ticket_id}/cancel")
async def admin_cancel_ticket(ticket_id: int, request: Request, db: DbSession, user: CurrentUser):
    """取消排队：WAITING/CALLED→CANCELLED。"""
    ticket = await _get_queue_ticket(db, ticket_id)
    if ticket.status not in ACTIVE_STATUSES:
        raise AppException(40021, "当前状态不可取消")
    ticket.status = "CANCELLED"
    ticket.cancelled_at = naive_local_now()
    await db.flush()
    return success({"ticket_no": ticket.ticket_no, "status": ticket.status}, request=request)

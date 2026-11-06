"""到店排队：顾客取号/查号/取消 + 员工叫号/落座/取消。"""
from datetime import date

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import naive_local_now, now_local, success, serialize_dt
from app.models import QueueTicket
from app.services.system_settings_service import SystemSettingsService
from app.services.websocket_manager import ws_manager
from app.services.store_scope import require_bound_store

router = APIRouter(tags=["排队"])

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
        "issued_at": serialize_dt(ticket.issued_at),
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
    # rollback 会 expire customer 等已加载对象，重试路径须用预取的普通值，
    # 避免在属性惰性加载的同步路径中发起 IO（MissingGreenlet）。
    customer_id = int(customer.id)
    for attempt in (1, 2):
        number = await _max_number(db, body.store_id, today) + 1
        candidate = QueueTicket(
            store_id=body.store_id,
            customer_id=customer_id,
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


def _assert_staff_ticket_store(user: CurrentUser, ticket: QueueTicket) -> None:
    """门店员工只能操作本店排队票；跨店 ID 与不存在统一 404。"""
    sid = require_bound_store(user)
    if sid is not None and int(ticket.store_id) != sid:
        raise AppException(40404, "排队号不存在", status_code=404)


@router.get("/admin/queue/today", dependencies=[require_permission("table:manage")])
async def admin_today_tickets(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int,
    status: str | None = None,
):
    """当日某门店全部排队号：WAITING/CALLED 在前按 number 正序。"""
    sid = require_bound_store(user, store_id)
    if sid is None:
        raise AppException(40008, "请选择门店")
    today = now_local().date()
    stmt = select(QueueTicket).where(
        QueueTicket.store_id == sid,
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
            "issued_at": serialize_dt(t.issued_at),
            "called_at": serialize_dt(t.called_at),
            "seated_at": serialize_dt(t.seated_at),
            "cancelled_at": serialize_dt(t.cancelled_at),
        }
        for t in rows
    ]
    return success(items, request=request)


@router.post("/admin/queue/{ticket_id}/call", dependencies=[require_permission("table:manage")])
async def admin_call_ticket(ticket_id: int, request: Request, db: DbSession, user: CurrentUser):
    """叫号：WAITING→CALLED，并广播到叫号屏 display 渠道。"""
    ticket = await _get_queue_ticket(db, ticket_id)
    _assert_staff_ticket_store(user, ticket)
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


@router.post("/admin/queue/{ticket_id}/seat", dependencies=[require_permission("table:manage")])
async def admin_seat_ticket(ticket_id: int, request: Request, db: DbSession, user: CurrentUser):
    """落座：CALLED→SEATED。"""
    ticket = await _get_queue_ticket(db, ticket_id)
    _assert_staff_ticket_store(user, ticket)
    if ticket.status != "CALLED":
        raise AppException(40021, "仅已叫号的排队号可标记落座")
    ticket.status = "SEATED"
    ticket.seated_at = naive_local_now()
    await db.flush()
    return success({"ticket_no": ticket.ticket_no, "status": ticket.status}, request=request)


@router.post("/admin/queue/{ticket_id}/cancel", dependencies=[require_permission("table:manage")])
async def admin_cancel_ticket(ticket_id: int, request: Request, db: DbSession, user: CurrentUser):
    """取消排队：WAITING/CALLED→CANCELLED。"""
    ticket = await _get_queue_ticket(db, ticket_id)
    _assert_staff_ticket_store(user, ticket)
    if ticket.status not in ACTIVE_STATUSES:
        raise AppException(40021, "当前状态不可取消")
    ticket.status = "CANCELLED"
    ticket.cancelled_at = naive_local_now()
    await db.flush()
    return success({"ticket_no": ticket.ticket_no, "status": ticket.status}, request=request)

"""电子发票：顾客提交申请 + 员工后台标记开票/驳回。"""
import re

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentCustomer, CurrentUser, DbSession
from app.core.exceptions import AppException
from app.core.response import calc_pages, money, naive_local_now, success
from app.models import InvoiceRequest, Order

router = APIRouter(prefix="/api", tags=["发票"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvoiceCreateBody(BaseModel):
    order_id: int = Field(..., gt=0)
    title_type: str = Field(default="PERSON", pattern=r"^(PERSON|COMPANY)$")
    title: str = Field(..., min_length=1, max_length=200)
    tax_no: str | None = Field(default=None, max_length=50)
    email: str = Field(..., min_length=3, max_length=100, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvoiceRejectBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)


@router.post("/miniprogram/invoices")
async def create_invoice(body: InvoiceCreateBody, request: Request, db: DbSession, customer: CurrentCustomer):
    """对已支付订单提交开票申请（每单仅一条未驳回申请）。"""
    order = await db.get(Order, body.order_id)
    if not order or int(order.customer_id or 0) != int(customer.id):
        raise AppException(40404, "订单不存在")
    if str(order.payment_status or "").upper() != "PAID":
        raise AppException(40010, "仅已支付订单可开票")
    title_type = (body.title_type or "PERSON").strip().upper()
    if title_type not in ("PERSON", "COMPANY"):
        raise AppException(40012, "抬头类型不正确")
    tax_no = (body.tax_no or "").strip() or None
    if title_type == "COMPANY" and not tax_no:
        raise AppException(40012, "单位抬头必须填写税号")
    email = (body.email or "").strip()
    if not _EMAIL_RE.match(email):
        raise AppException(40012, "邮箱格式不正确")
    dup = (
        await db.execute(
            select(InvoiceRequest).where(
                InvoiceRequest.order_id == order.id,
                InvoiceRequest.status.in_(("PENDING", "ISSUED")),
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise AppException(40011, "该订单已申请过发票")
    inv = InvoiceRequest(
        order_id=order.id,
        order_no=order.order_no,
        store_id=order.store_id,
        customer_id=customer.id,
        title_type=title_type,
        title=body.title.strip(),
        tax_no=tax_no,
        email=email,
        amount=money(order.total_amount),
        status="PENDING",
    )
    db.add(inv)
    try:
        await db.flush()
    except IntegrityError:
        # UNIQUE(order_id) 并发兜底
        await db.rollback()
        raise AppException(40011, "该订单已申请过发票")
    return success({"request_id": int(inv.id), "status": inv.status}, request=request)


@router.get("/miniprogram/invoices")
async def list_my_invoices(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    page: int = 1,
    page_size: int = 10,
):
    """我的开票申请列表（倒序分页）。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    base = select(InvoiceRequest).where(InvoiceRequest.customer_id == customer.id)
    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = (
        (
            await db.execute(
                base.order_by(InvoiceRequest.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [
        {
            "request_id": int(r.id),
            "order_id": int(r.order_id),
            "order_no": r.order_no,
            "title_type": r.title_type,
            "title": r.title,
            "tax_no": r.tax_no,
            "email": r.email,
            "amount": str(money(r.amount)),
            "status": r.status,
            "reject_reason": r.reject_reason,
            "created_at": r.created_at.isoformat() if r.created_at else None,
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


@router.get("/invoices/admin")
async def admin_list_invoices(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    status: str | None = None,
    store_id: int | None = None,
    page: int = 1,
    page_size: int = 10,
):
    """后台发票申请列表（真分页，id 倒序兜底）。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    base = select(InvoiceRequest)
    if status:
        base = base.where(InvoiceRequest.status == status.strip().upper())
    if store_id is not None:
        base = base.where(InvoiceRequest.store_id == int(store_id))
    total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one())
    rows = (
        (
            await db.execute(
                base.order_by(InvoiceRequest.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [
        {
            "request_id": int(r.id),
            "order_id": int(r.order_id),
            "order_no": r.order_no,
            "store_id": int(r.store_id),
            "customer_id": int(r.customer_id) if r.customer_id else None,
            "title_type": r.title_type,
            "title": r.title,
            "tax_no": r.tax_no,
            "email": r.email,
            "amount": str(money(r.amount)),
            "status": r.status,
            "reject_reason": r.reject_reason,
            "issued_at": r.issued_at.isoformat() if r.issued_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
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


async def _get_pending_invoice(db, request_id: int) -> InvoiceRequest:
    inv = await db.get(InvoiceRequest, request_id)
    if not inv:
        raise AppException(40404, "发票申请不存在")
    if inv.status != "PENDING":
        raise AppException(40013, "仅待处理的发票申请可操作")
    return inv


@router.post("/invoices/admin/{request_id}/issue")
async def admin_issue_invoice(request_id: int, request: Request, db: DbSession, user: CurrentUser):
    """标记已开票。"""
    inv = await _get_pending_invoice(db, request_id)
    inv.status = "ISSUED"
    inv.issued_at = naive_local_now()
    await db.flush()
    return success({"request_id": int(inv.id), "status": inv.status}, request=request)


@router.post("/invoices/admin/{request_id}/reject")
async def admin_reject_invoice(
    request_id: int,
    body: InvoiceRejectBody,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """驳回开票申请。"""
    inv = await _get_pending_invoice(db, request_id)
    inv.status = "REJECTED"
    inv.reject_reason = body.reason.strip()
    await db.flush()
    return success({"request_id": int(inv.id), "status": inv.status}, request=request)

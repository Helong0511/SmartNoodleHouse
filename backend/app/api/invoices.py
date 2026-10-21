"""电子发票：顾客提交申请 + 员工后台标记开票/驳回。"""
import re

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import calc_pages, money, naive_local_now, success, serialize_dt
from app.models import Customer, InvoiceRequest, Order, Store
from app.services.audit_service import AuditService
from app.services.customer_service import CustomerService
from app.services.store_scope import assert_store_access, require_bound_store
from app.utils.tax_no import normalize_tax_no, tax_no_error_message

router = APIRouter(tags=["发票"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvoiceCreateBody(BaseModel):
    order_id: int = Field(..., gt=0)
    title_type: str = Field(default="PERSON", pattern=r"^(PERSON|COMPANY)$")
    title: str = Field(..., min_length=1, max_length=200)
    tax_no: str | None = Field(default=None, max_length=50)
    # 邮箱格式由业务层校验（返回 40012），此处不设 pattern 以免 pydantic 先拦成 422/40001
    email: str = Field(..., min_length=3, max_length=100)


class InvoiceRejectBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=255)


class AdminInvoiceCreateBody(BaseModel):
    """员工为指定已支付订单代开票（个别顾客现场/电话申请）。"""

    order_no: str | None = Field(default=None, max_length=40, description="订单号（与 order_id 二选一）")
    order_id: int | None = Field(default=None, gt=0, description="订单 ID（与 order_no 二选一）")
    title_type: str = Field(default="PERSON", pattern=r"^(PERSON|COMPANY)$")
    title: str = Field(..., min_length=1, max_length=200)
    tax_no: str | None = Field(default=None, max_length=50)
    email: str = Field(..., min_length=3, max_length=100)
    mark_issued: bool = Field(default=False, description="创建后直接标记已开票")


def _normalize_title_fields(*, title_type: str, title: str, tax_no: str | None, email: str) -> tuple[str, str, str | None, str]:
    tt = (title_type or "PERSON").strip().upper()
    if tt not in ("PERSON", "COMPANY"):
        raise AppException(40012, "抬头类型不正确")
    tn = normalize_tax_no(tax_no) or None
    if tt == "COMPANY":
        err = tax_no_error_message(tn)
        if err:
            raise AppException(40012, err)
    else:
        tn = None
    em = (email or "").strip()
    if not _EMAIL_RE.match(em):
        raise AppException(40012, "邮箱格式不正确")
    tit = (title or "").strip()
    if not tit:
        raise AppException(40012, "请填写发票抬头")
    return tt, tit, tn, em


async def _upsert_invoice_for_order(
    db,
    *,
    order: Order,
    title_type: str,
    title: str,
    tax_no: str | None,
    email: str,
    status: str = "PENDING",
) -> InvoiceRequest:
    amount = money(order.total_amount)
    existing = (
        await db.execute(select(InvoiceRequest).where(InvoiceRequest.order_id == order.id))
    ).scalar_one_or_none()
    if existing is not None:
        st = str(existing.status or "").upper()
        if st in ("PENDING", "ISSUED"):
            raise AppException(40011, "该订单已申请过发票")
        existing.title_type = title_type
        existing.title = title
        existing.tax_no = tax_no
        existing.email = email
        existing.amount = amount
        existing.status = status
        existing.reject_reason = None
        existing.issued_at = naive_local_now() if status == "ISSUED" else None
        existing.customer_id = order.customer_id
        existing.store_id = order.store_id
        existing.order_no = order.order_no
        await db.flush()
        # server_default / onupdate 的时间字段需 refresh，否则序列化会触发 MissingGreenlet
        await db.refresh(existing)
        return existing

    inv = InvoiceRequest(
        order_id=order.id,
        order_no=order.order_no,
        store_id=order.store_id,
        customer_id=order.customer_id,
        title_type=title_type,
        title=title,
        tax_no=tax_no,
        email=email,
        amount=amount,
        status=status,
        issued_at=naive_local_now() if status == "ISSUED" else None,
    )
    db.add(inv)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise AppException(40011, "该订单已申请过发票")
    await db.refresh(inv)
    return inv


def _serialize_customer_item(r: InvoiceRequest) -> dict:
    return {
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
        "issued_at": serialize_dt(r.issued_at),
        "created_at": serialize_dt(r.created_at),
    }


def _serialize_admin_item(r: InvoiceRequest, store_name: str | None = None) -> dict:
    item = _serialize_customer_item(r)
    item.update(
        {
            "store_id": int(r.store_id),
            "store_name": store_name,
            "customer_id": int(r.customer_id) if r.customer_id else None,
        }
    )
    return item


def _profile_payload(inv: InvoiceRequest | None) -> dict | None:
    if not inv:
        return None
    return {
        "title_type": inv.title_type,
        "title": inv.title,
        "tax_no": inv.tax_no,
        "email": inv.email,
        "source_order_no": inv.order_no,
        "source_status": inv.status,
    }


async def _latest_profile_by_customer(db, customer_id: int | None) -> InvoiceRequest | None:
    if not customer_id:
        return None
    return (
        await db.execute(
            select(InvoiceRequest)
            .where(InvoiceRequest.customer_id == int(customer_id))
            .order_by(InvoiceRequest.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _latest_profiles_map(db, customer_ids: list[int]) -> dict[int, InvoiceRequest]:
    """每位顾客取最近一条发票抬头记录。"""
    ids = sorted({int(x) for x in customer_ids if x})
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(InvoiceRequest)
            .where(InvoiceRequest.customer_id.in_(ids))
            .order_by(InvoiceRequest.customer_id.asc(), InvoiceRequest.id.desc())
        )
    ).scalars().all()
    out: dict[int, InvoiceRequest] = {}
    for inv in rows:
        cid = int(inv.customer_id or 0)
        if cid and cid not in out:
            out[cid] = inv
    return out


@router.get("/admin/invoices/pending-summary", dependencies=[require_permission("invoice:manage")])
async def admin_invoice_pending_summary(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=5, ge=1, le=20),
):
    """数据概览待办：待开票申请数量 + 最近几条。"""
    sid = require_bound_store(user, store_id)
    base = select(InvoiceRequest).where(InvoiceRequest.status == "PENDING")
    if sid is not None:
        base = base.where(InvoiceRequest.store_id == int(sid))
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()
        or 0
    )
    rows = (
        await db.execute(base.order_by(InvoiceRequest.id.desc()).limit(limit))
    ).scalars().all()
    store_ids = list({int(r.store_id) for r in rows})
    store_map: dict[int, str] = {}
    if store_ids:
        store_map = {
            int(s.id): s.name
            for s in (
                await db.execute(select(Store).where(Store.id.in_(store_ids)))
            ).scalars().all()
        }
    return success(
        {
            "pending_count": total,
            "items": [
                _serialize_admin_item(r, store_name=store_map.get(int(r.store_id)))
                for r in rows
            ],
        },
        request=request,
    )


def _eligible_paid_order_query(sid: int | None):
    """已支付且无 PENDING/ISSUED 发票的订单。"""
    blocked = select(InvoiceRequest.order_id).where(
        InvoiceRequest.status.in_(("PENDING", "ISSUED"))
    )
    q = select(Order).where(
        Order.payment_status == "PAID",
        Order.id.notin_(blocked),
    )
    if sid is not None:
        q = q.where(Order.store_id == int(sid))
    return q


@router.get("/admin/invoices/customer-candidates", dependencies=[require_permission("invoice:manage")])
async def admin_invoice_customer_candidates(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    keyword: str | None = Query(default=None, max_length=64),
    store_id: int | None = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
):
    """代开票：先选顾客（仅列出仍有可开票已付订单的顾客）。"""
    sid = require_bound_store(user, store_id)
    eligible = _eligible_paid_order_query(sid).subquery()
    # 按顾客聚合可开票订单数；排除无顾客绑定
    agg = (
        select(
            eligible.c.customer_id.label("customer_id"),
            func.count(eligible.c.id).label("order_count"),
            func.max(eligible.c.created_at).label("last_order_at"),
        )
        .where(eligible.c.customer_id.is_not(None))
        .group_by(eligible.c.customer_id)
    ).subquery()
    q = select(Customer, agg.c.order_count, agg.c.last_order_at).join(
        agg, agg.c.customer_id == Customer.id
    )
    kw = (keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        q = q.where(
            or_(
                Customer.phone.like(like),
                Customer.nickname.like(like),
                Customer.display_name.like(like),
                Customer.customer_no.like(like),
            )
        )
    total = int((await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one() or 0)
    rows = (
        await db.execute(
            q.order_by(agg.c.last_order_at.desc(), Customer.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    cust_ids = [int(c.id) for c, _, _ in rows]
    profile_map = await _latest_profiles_map(db, cust_ids)
    items = []
    for cust, order_count, last_order_at in rows:
        profile = profile_map.get(int(cust.id))
        items.append(
            {
                "customer_id": int(cust.id),
                "customer_no": cust.customer_no,
                **CustomerService.staff_customer_name_fields(cust),
                "customer_phone": cust.phone,
                "eligible_order_count": int(order_count or 0),
                "last_order_at": serialize_dt(last_order_at),
                "has_profile": profile is not None,
                "last_profile": _profile_payload(profile),
            }
        )
    # 未绑定顾客但仍有可开票订单时，单独给出入口
    guest_q = _eligible_paid_order_query(sid).where(Order.customer_id.is_(None))
    if not kw:
        guest_count = int(
            (await db.execute(select(func.count()).select_from(guest_q.subquery()))).scalar_one() or 0
        )
        if guest_count and page == 1:
            items.insert(
                0,
                {
                    "customer_id": 0,
                    "customer_no": None,
                    "customer_display_name": "未绑定顾客",
                    "customer_nickname": None,
                    "customer_name": "未绑定顾客",
                    "customer_phone": None,
                    "eligible_order_count": guest_count,
                    "last_order_at": None,
                    "has_profile": False,
                    "last_profile": None,
                },
            )
    return success(
        {
            "items": items,
            "total": total + (1 if (not kw and page == 1 and any(i["customer_id"] == 0 for i in items)) else 0),
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
        },
        request=request,
    )


@router.get("/admin/invoices/order-candidates", dependencies=[require_permission("invoice:manage")])
async def admin_invoice_order_candidates(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    keyword: str | None = Query(default=None, max_length=64),
    customer_id: int | None = Query(default=None, ge=0, description="0=未绑定顾客；>0=指定顾客"),
    store_id: int | None = Query(default=None, ge=1),
    sort: str = Query(
        default="date_desc",
        pattern="^(date_desc|date_asc|amount_desc|amount_asc|order_no_asc|order_no_desc)$",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
):
    """代开票可选订单：已支付且无 PENDING/ISSUED 发票；可按顾客过滤并排序。"""
    sid = require_bound_store(user, store_id)
    q = _eligible_paid_order_query(sid)
    if customer_id is not None:
        if int(customer_id) == 0:
            q = q.where(Order.customer_id.is_(None))
        else:
            q = q.where(Order.customer_id == int(customer_id))
    kw = (keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        if customer_id is None:
            q = q.outerjoin(Customer, Customer.id == Order.customer_id).where(
                or_(
                    Order.order_no.like(like),
                    Customer.phone.like(like),
                    Customer.nickname.like(like),
                    Customer.display_name.like(like),
                    Customer.customer_no.like(like),
                )
            )
        else:
            q = q.where(Order.order_no.like(like))
    order_by = {
        "date_desc": (Order.created_at.desc(), Order.id.desc()),
        "date_asc": (Order.created_at.asc(), Order.id.asc()),
        "amount_desc": (Order.total_amount.desc(), Order.created_at.desc()),
        "amount_asc": (Order.total_amount.asc(), Order.created_at.desc()),
        "order_no_asc": (Order.order_no.asc(),),
        "order_no_desc": (Order.order_no.desc(),),
    }.get(sort, (Order.created_at.desc(), Order.id.desc()))
    total = int((await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one() or 0)
    orders = list(
        (
            await db.execute(
                q.order_by(*order_by).offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars().all()
    )
    store_ids = list({int(o.store_id) for o in orders if o.store_id})
    cust_ids = list({int(o.customer_id) for o in orders if o.customer_id})
    store_map: dict[int, Store] = {}
    if store_ids:
        store_map = {
            int(s.id): s
            for s in (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars().all()
        }
    cust_map: dict[int, Customer] = {}
    if cust_ids:
        cust_map = {
            int(c.id): c
            for c in (await db.execute(select(Customer).where(Customer.id.in_(cust_ids)))).scalars().all()
        }
    profile_map = await _latest_profiles_map(db, cust_ids)
    items = []
    for o in orders:
        cust = cust_map.get(int(o.customer_id)) if o.customer_id else None
        store = store_map.get(int(o.store_id)) if o.store_id else None
        profile = profile_map.get(int(o.customer_id)) if o.customer_id else None
        items.append(
            {
                "order_id": int(o.id),
                "order_no": o.order_no,
                "store_id": int(o.store_id) if o.store_id else None,
                "store_name": store.name if store else None,
                "customer_id": int(o.customer_id) if o.customer_id else None,
                **CustomerService.staff_customer_name_fields(cust),
                "customer_phone": cust.phone if cust else None,
                "amount": str(money(o.total_amount)),
                "created_at": serialize_dt(o.created_at),
                "has_profile": profile is not None,
                "last_profile": _profile_payload(profile),
            }
        )
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


@router.get("/admin/invoices/last-profile", dependencies=[require_permission("invoice:manage")])
async def admin_invoice_last_profile(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    order_id: int | None = Query(default=None, ge=1),
    order_no: str | None = Query(default=None, max_length=40),
    customer_id: int | None = Query(default=None, ge=1),
):
    """按订单或顾客取最近一次填写的开票信息，供代开票自动填充。"""
    cust_id = customer_id
    order: Order | None = None
    if order_id or (order_no or "").strip():
        if order_id:
            order = await db.get(Order, int(order_id))
        else:
            order = (
                await db.execute(select(Order).where(Order.order_no == (order_no or "").strip()))
            ).scalar_one_or_none()
        if not order:
            raise AppException(40404, "订单不存在")
        assert_store_access(user, order.store_id)
        cust_id = int(order.customer_id) if order.customer_id else None
    elif cust_id:
        # 无订单时仅按顾客查；门店员不额外限制（抬头属顾客维度）
        pass
    else:
        raise AppException(40012, "请指定订单或顾客")
    inv = await _latest_profile_by_customer(db, cust_id)
    return success(
        {
            "customer_id": int(cust_id) if cust_id else None,
            "order_id": int(order.id) if order else None,
            "order_no": order.order_no if order else None,
            "profile": _profile_payload(inv),
        },
        request=request,
    )


@router.post("/miniprogram/invoices")
async def create_invoice(body: InvoiceCreateBody, request: Request, db: DbSession, customer: CurrentCustomer):
    """对已支付订单提交开票申请（每单仅一条有效申请；驳回后可重提）。"""
    from app.services.system_settings_service import SystemSettingsService

    order = await db.get(Order, body.order_id)
    if not order or int(order.customer_id or 0) != int(customer.id):
        raise AppException(40404, "订单不存在")
    if str(order.payment_status or "").upper() != "PAID":
        raise AppException(40010, "仅已支付订单可开票")
    # 按订单所属门店读开关（可门店覆盖；未覆盖则回退平台默认）
    if not await SystemSettingsService.effective_bool(
        db, "invoice_enabled", False, store_id=int(order.store_id or 0) or None
    ):
        raise AppException(40020, "电子发票功能未开启")
    title_type, title, tax_no, email = _normalize_title_fields(
        title_type=body.title_type,
        title=body.title,
        tax_no=body.tax_no,
        email=body.email,
    )
    inv = await _upsert_invoice_for_order(
        db,
        order=order,
        title_type=title_type,
        title=title,
        tax_no=tax_no,
        email=email,
        status="PENDING",
    )
    return success({"request_id": int(inv.id), "status": inv.status}, request=request)


@router.get("/miniprogram/invoices")
async def list_my_invoices(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    page: int = 1,
    page_size: int = 10,
    order_id: int | None = None,
):
    """我的开票申请列表（倒序分页）；可按 order_id 查单笔。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    base = select(InvoiceRequest).where(InvoiceRequest.customer_id == customer.id)
    if order_id is not None:
        base = base.where(InvoiceRequest.order_id == int(order_id))
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
    items = [_serialize_customer_item(r) for r in rows]
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


@router.post("/admin/invoices", dependencies=[require_permission("invoice:manage")])
async def admin_create_invoice(
    body: AdminInvoiceCreateBody,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """员工为个别顾客代开票：按订单号/ID 创建申请，可选直接标记已开票。"""
    order_no = (body.order_no or "").strip()
    order: Order | None = None
    if body.order_id:
        order = await db.get(Order, int(body.order_id))
    elif order_no:
        order = (
            await db.execute(select(Order).where(Order.order_no == order_no))
        ).scalar_one_or_none()
    else:
        raise AppException(40012, "请填写订单号")
    if not order:
        raise AppException(40404, "订单不存在")
    assert_store_access(user, order.store_id)
    if str(order.payment_status or "").upper() != "PAID":
        raise AppException(40010, "仅已支付订单可开票")
    title_type, title, tax_no, email = _normalize_title_fields(
        title_type=body.title_type,
        title=body.title,
        tax_no=body.tax_no,
        email=body.email,
    )
    status = "ISSUED" if body.mark_issued else "PENDING"
    inv = await _upsert_invoice_for_order(
        db,
        order=order,
        title_type=title_type,
        title=title,
        tax_no=tax_no,
        email=email,
        status=status,
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="invoice.create" if status == "PENDING" else "invoice.create_issued",
        module="invoice",
        target_type="invoice_request",
        target_id=int(inv.id),
        detail={
            "order_no": order.order_no,
            "email": email,
            "title": title,
            "status": status,
            "source": "admin",
        },
        request=request,
        store_id=int(order.store_id),
    )
    store = await db.get(Store, order.store_id)
    return success(
        _serialize_admin_item(inv, store_name=store.name if store else None),
        request=request,
    )


@router.get("/admin/invoices", dependencies=[require_permission("invoice:manage")])
async def admin_list_invoices(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    status: str | None = None,
    store_id: int | None = None,
    page: int = 1,
    page_size: int = 10,
):
    """后台发票申请列表（真分页，门店隔离，id 倒序）。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    sid = require_bound_store(user, store_id)
    base = select(InvoiceRequest, Store.name).outerjoin(Store, Store.id == InvoiceRequest.store_id)
    if status:
        base = base.where(InvoiceRequest.status == status.strip().upper())
    if sid is not None:
        base = base.where(InvoiceRequest.store_id == int(sid))
    count_q = select(func.count()).select_from(InvoiceRequest)
    if status:
        count_q = count_q.where(InvoiceRequest.status == status.strip().upper())
    if sid is not None:
        count_q = count_q.where(InvoiceRequest.store_id == int(sid))
    total = int((await db.execute(count_q)).scalar_one())
    rows = (
        (
            await db.execute(
                base.order_by(InvoiceRequest.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .all()
    )
    items = [_serialize_admin_item(r, store_name=name) for r, name in rows]
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


async def _get_pending_invoice(db, request_id: int, user) -> InvoiceRequest:
    inv = await db.get(InvoiceRequest, request_id)
    if not inv:
        raise AppException(40404, "发票申请不存在")
    assert_store_access(user, inv.store_id)
    if inv.status != "PENDING":
        raise AppException(40013, "仅待处理的发票申请可操作")
    return inv


@router.post("/admin/invoices/{request_id}/issue", dependencies=[require_permission("invoice:manage")])
async def admin_issue_invoice(request_id: int, request: Request, db: DbSession, user: CurrentUser):
    """标记已开票。"""
    inv = await _get_pending_invoice(db, request_id, user)
    inv.status = "ISSUED"
    inv.issued_at = naive_local_now()
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="invoice.issue",
        module="invoice",
        target_type="invoice_request",
        target_id=int(inv.id),
        detail={"order_no": inv.order_no, "email": inv.email, "amount": str(inv.amount)},
        request=request,
        store_id=int(inv.store_id),
    )
    return success({"request_id": int(inv.id), "status": inv.status}, request=request)


@router.post("/admin/invoices/{request_id}/reject", dependencies=[require_permission("invoice:manage")])
async def admin_reject_invoice(
    request_id: int,
    body: InvoiceRejectBody,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """驳回开票申请。"""
    inv = await _get_pending_invoice(db, request_id, user)
    reason = body.reason.strip()
    inv.status = "REJECTED"
    inv.reject_reason = reason
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="invoice.reject",
        module="invoice",
        target_type="invoice_request",
        target_id=int(inv.id),
        detail={"order_no": inv.order_no, "reason": reason},
        request=request,
        store_id=int(inv.store_id),
    )
    return success({"request_id": int(inv.id), "status": inv.status}, request=request)

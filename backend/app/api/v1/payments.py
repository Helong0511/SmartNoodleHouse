from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, StaffOrCustomer, require_permission, require_pos
from app.core.exceptions import AppException
from app.core.response import calc_pages, success
from app.models import Order, Payment, PaymentRefund
from app.schemas.order import PaymentCreate, PosCashPay, PosScanPay
from app.services.customer_service import CustomerService
from app.services.payment_service import PaymentService
from app.services.store_scope import assert_order_store_access, require_bound_store
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/payments", tags=["支付"])


def _apply_date_range(q, col, date_from: str | None, date_to: str | None):
    if date_from:
        try:
            q = q.where(col >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            end = datetime.fromisoformat(date_to)
            if len(date_to) <= 10:
                end = end + timedelta(days=1)
            q = q.where(col < end)
        except ValueError:
            pass
    return q


class RefundCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    reason: str = Field(default="商家退款", max_length=200)


async def _wechat_openid_for_customer(db: AsyncSession, customer_id: int) -> str | None:
    return await CustomerService.resolve_wechat_openid(db, customer_id)


async def _alipay_buyer_id_for_customer(db: AsyncSession, customer_id: int) -> str | None:
    return await CustomerService.resolve_alipay_user_id(db, customer_id)


@router.post("/orders/{order_id}/pay")
async def create_payment(
    order_id: int,
    data: PaymentCreate,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    order = await db.get(Order, order_id)
    if not order:
        return success(None, message="订单不存在", request=request)
    from app.services.order_access import assert_customer_can_pay_order

    # 禁止顾客 claim 未绑定的 POS 订单；仅本人订单可支付
    assert_customer_can_pay_order(order, customer.id)
    extra: dict | None = None
    provider_u = data.provider.upper()
    channel_l = data.channel_type.lower()
    if provider_u == "WECHAT" and channel_l == "miniprogram":
        openid = await _wechat_openid_for_customer(db, customer.id)
        if openid:
            extra = {"openid": openid}
    elif provider_u == "ALIPAY" and channel_l == "miniprogram":
        buyer_id = await _alipay_buyer_id_for_customer(db, customer.id)
        if buyer_id:
            extra = {"buyer_id": buyer_id}
    payment, pay_data = await PaymentService.create_payment(
        db, order, data.provider, data.channel_type, customer.id, extra=extra
    )
    if pay_data.get("mock_settled"):
        await ws_manager.notify_order_event(
            "ORDER_PAID",
            {"order_id": order.id, "order_no": order.order_no, "store_id": order.store_id},
        )
        if order.customer_id:
            from app.models import Customer
            from app.services.customer_service import CustomerService

            cust = await db.get(Customer, order.customer_id)
            if cust:
                profile = await CustomerService.build_member_profile(db, cust)
                pay_data["points_earned"] = int(order.total_amount or 0)
                pay_data["member_points"] = profile.get("member_points", 0)
                pay_data["member_level_name"] = profile.get("member_level_name")
    return success({"payment_no": payment.payment_no, "amount": str(payment.amount), **pay_data}, request=request)


@router.post("/orders/{order_id}/sync")
async def sync_order_payment(
    order_id: int,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    provider: str | None = None,
):
    """主动查单：按订单同步最近一笔待支付单（防回调丢失/支付结果不明）。"""
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    from app.services.order_access import assert_customer_owns_order

    assert_customer_owns_order(order, customer.id, action="同步支付")
    payment, meta = await PaymentService.sync_order_pending_payment(
        db, order, provider=provider
    )
    if meta.get("synced") and payment and payment.status == "PAID":
        await _notify_paid_or_refunded(db, payment)
    return success(
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "payment_no": payment.payment_no if payment else meta.get("payment_no"),
            "payment_status": order.payment_status,
            "order_status": order.status,
            **meta,
        },
        request=request,
    )


@router.post("/{payment_no}/sync")
async def sync_payment(
    payment_no: str,
    request: Request,
    db: DbSession,
    auth: StaffOrCustomer,
):
    """主动向微信/支付宝查单并幂等入账。"""
    user, customer = auth
    result = await db.execute(select(Payment).where(Payment.payment_no == payment_no))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="支付记录不存在")
    order = await db.get(Order, payment.order_id)
    if customer:
        if payment.customer_id and payment.customer_id != customer.id:
            raise HTTPException(status_code=403, detail="无权同步此支付")
        if order and order.customer_id and order.customer_id != customer.id:
            raise HTTPException(status_code=403, detail="无权同步此支付")
    if user is not None and order:
        assert_order_store_access(user, order)
    before = payment.status
    payment, meta = await PaymentService.sync_payment_status(db, payment)
    if before != "PAID" and payment.status == "PAID":
        await _notify_paid_or_refunded(db, payment)
    progress = await PaymentService.payment_progress(db, order) if order else {}
    return success(
        {
            "payment_no": payment.payment_no,
            "status": payment.status,
            "provider": payment.provider,
            "amount": str(payment.amount),
            **meta,
            **progress,
        },
        request=request,
    )


@router.post("/orders/{order_id}/cash", dependencies=[require_pos()])
async def pay_order_cash(
    order_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    data: PosCashPay | None = None,
):
    """收银台现金收款（支持分笔）：amount=本笔入账，received_amount=实收用于找零。"""
    body = data or PosCashPay()
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    assert_order_store_access(user, order)
    payment, pay_data = await PaymentService.settle_cash_payment(
        db,
        order,
        operator_id=user.id,
        amount=body.amount,
        received_amount=body.received_amount,
        change_amount=None,
    )
    if pay_data.get("fully_paid"):
        await ws_manager.notify_order_event(
            "ORDER_PAID",
            {"order_id": order.id, "order_no": order.order_no, "store_id": order.store_id, "channel": order.channel},
        )
    # amount / 进度字段放最后，避免被 pay_data 内同名键覆盖
    return success(
        {
            "payment_no": payment.payment_no,
            "provider": "CASH",
            "order_id": order.id,
            "order_no": order.order_no,
            "status": order.status,
            "received_amount": str(body.received_amount) if body.received_amount is not None else None,
            **pay_data,
            "amount": str(payment.amount),
            "change_amount": pay_data.get("change_amount")
            or (
                str(body.received_amount - payment.amount)
                if body.received_amount is not None
                else None
            ),
            "fully_paid": bool(pay_data.get("fully_paid")),
            "remaining_amount": pay_data.get("remaining_amount"),
            "paid_amount": pay_data.get("paid_amount"),
            "payment_status": pay_data.get("payment_status"),
        },
        request=request,
    )


@router.post("/orders/{order_id}/pos-scan", dependencies=[require_pos()])
async def create_pos_scan_payment(
    order_id: int,
    data: PosScanPay,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """收银台扫顾客付款码（支持分笔；自动识别微信/支付宝）。"""
    from app.services.payment_barcode import resolve_barcode_provider

    auth_code = (data.auth_code or "").strip()
    provider = resolve_barcode_provider(auth_code, data.provider)
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    assert_order_store_access(user, order)
    channel_type = "micropay" if provider == "WECHAT" else "barcode"
    payment, pay_data = await PaymentService.create_payment(
        db,
        order,
        provider,
        channel_type,
        customer_id=None,
        operator_id=user.id,
        extra={"auth_code": auth_code},
        amount=data.amount,
    )
    if pay_data.get("fully_paid") and (pay_data.get("mock_settled") or pay_data.get("status") == "PAID"):
        await ws_manager.notify_order_event(
            "ORDER_PAID",
            {
                "order_id": order.id,
                "order_no": order.order_no,
                "store_id": order.store_id,
                "channel": order.channel,
            },
        )
    return success(
        {
            "payment_no": payment.payment_no,
            "provider": provider,
            "order_id": order.id,
            "order_no": order.order_no,
            **pay_data,
            # 显式压后：本笔金额与结清标志，防止被 pay_data 覆盖成整单语义
            "amount": str(payment.amount),
            "fully_paid": bool(pay_data.get("fully_paid")),
            "remaining_amount": pay_data.get("remaining_amount"),
            "paid_amount": pay_data.get("paid_amount"),
            "payment_status": pay_data.get("payment_status"),
        },
        request=request,
    )


@router.get("/orders/{order_id}/progress", dependencies=[require_pos()])
async def order_payment_progress(
    order_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """收银组合收款进度：应收/已收/待收及已收明细。"""
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    assert_order_store_access(user, order)
    return success(await PaymentService.payment_progress(db, order), request=request)


@router.post("/{payment_no}/refund", dependencies=[require_permission("payment:refund")])
async def refund_payment(
    payment_no: str,
    data: RefundCreate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """管理端直接退款（仅店长/超管）。

    审批策略：
    - 收银员无 payment:refund，且本接口显式拒绝 CASHIER；收银渠道须走 POS 退款申请→店长审批。
    - 店长/超管可对任意渠道（含 POS）直连退款；POS 日常流程仍推荐「退款审批」页。
    """
    if user.role.code == "CASHIER":
        raise AppException(40301, "收银员不可直接退款，请在本班订单申请，由店长审批执行")
    result = await db.execute(select(Payment).where(Payment.payment_no == payment_no))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="支付记录不存在")
    order = await db.get(Order, payment.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if (order.channel or "").upper() == "POS" and user.role.code not in ("SUPER_ADMIN", "STORE_ADMIN"):
        raise AppException(40301, "收银渠道订单请走退款审批（店长/管理员执行）")
    assert_order_store_access(user, order)
    row = await PaymentService.refund(db, payment, data.amount, data.reason, operator_id=user.id)
    from app.services.audit_service import AuditService

    await AuditService.log_refund(
        db,
        user_id=user.id,
        refund_no=row.refund_no,
        payment_no=payment.payment_no,
        amount=row.amount,
        reason=data.reason,
        request=request,
        extra={"order_id": order.id, "status": row.status},
    )
    if row.status == "SUCCESS":
        await ws_manager.notify_order_event(
            "ORDER_REFUNDED",
            {
                "order_id": order.id,
                "order_no": order.order_no,
                "payment_no": payment.payment_no,
                "refund_no": row.refund_no,
                "amount": str(row.amount),
                "store_id": order.store_id,
            },
        )
    return success(
        {
            "refund_no": row.refund_no,
            "status": row.status,
            "amount": str(row.amount),
            "message": (
                "退款成功"
                if row.status == "SUCCESS"
                else "退款已受理，等待平台确认"
                if row.status in ("PENDING", "PROCESSING")
                else f"退款状态: {row.status}"
            ),
        },
        request=request,
    )


@router.post("/refunds/{refund_no}/sync", dependencies=[require_permission("payment:refund")])
async def sync_refund(refund_no: str, request: Request, db: DbSession, user: CurrentUser):
    """主动向平台查询退款结果（补偿回调）。"""
    row = (await db.execute(select(PaymentRefund).where(PaymentRefund.refund_no == refund_no))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="退款单不存在")
    order = await db.get(Order, row.order_id)
    if order:
        assert_order_store_access(user, order)
    row = await PaymentService.sync_refund_status(db, row)
    if row.status == "SUCCESS" and order:
        await ws_manager.notify_order_event(
            "ORDER_REFUNDED",
            {
                "order_id": order.id,
                "order_no": order.order_no,
                "refund_no": row.refund_no,
                "amount": str(row.amount),
                "store_id": order.store_id,
            },
        )
    return success(
        {"refund_no": row.refund_no, "status": row.status, "amount": str(row.amount)},
        request=request,
    )


async def _notify_paid_or_refunded(db: DbSession, result) -> None:
    from app.models import PaymentRefund as PR

    if isinstance(result, PR) and result.status == "SUCCESS":
        order = await db.get(Order, result.order_id)
        if order:
            await ws_manager.notify_order_event(
                "ORDER_REFUNDED",
                {
                    "order_id": order.id,
                    "order_no": order.order_no,
                    "refund_no": result.refund_no,
                    "amount": str(result.amount),
                    "store_id": order.store_id,
                },
            )
    elif isinstance(result, Payment) and result.status == "PAID":
        order = await db.get(Order, result.order_id)
        if order:
            await ws_manager.notify_order_event(
                "ORDER_PAID",
                {"order_id": order.id, "order_no": order.order_no, "store_id": order.store_id},
            )


@router.post("/wechat/notify")
async def wechat_notify(request: Request, db: DbSession):
    """支付与退款共用入口（按 resource / event_type 分流）。"""
    body = await request.body()
    headers = dict(request.headers)
    result = await PaymentService.handle_callback(db, "WECHAT", headers, body)
    await _notify_paid_or_refunded(db, result)
    return {"code": "SUCCESS", "message": "成功"}


@router.post("/wechat/refund-notify")
async def wechat_refund_notify(request: Request, db: DbSession):
    """可选独立退款回调地址（与 WECHAT_REFUND_NOTIFY_URL 对应）。"""
    return await wechat_notify(request, db)


@router.post("/alipay/notify")
async def alipay_notify(request: Request, db: DbSession):
    """支付回调；退款异步也会打到此地址（退款接口本身不设 notify_url）。"""
    body = await request.body()
    headers = dict(request.headers)
    result = await PaymentService.handle_callback(db, "ALIPAY", headers, body)
    await _notify_paid_or_refunded(db, result)
    return "success"


@router.get("", dependencies=[require_permission("payment:read")])
async def list_payments(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    store_id: int | None = None,
    keyword: str | None = None,
    provider: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """支付列表。keyword 匹配支付号/订单号；date_* 按支付时间 paid_at（空则 created_at）。"""
    from app.models import Store
    from app.schemas.pagination import clamp_page

    page, page_size = clamp_page(page, page_size)
    q = select(Payment).join(Order, Order.id == Payment.order_id)
    if status:
        q = q.where(Payment.status == status)
    # 超管可传 store_id 筛单店，不传看全店；非超管强制本店
    effective_store = require_bound_store(user, store_id)
    if effective_store is not None:
        q = q.where(Order.store_id == effective_store)
    if provider:
        q = q.where(Payment.provider == provider.strip().upper())
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = q.where(or_(Payment.payment_no.like(kw), Order.order_no.like(kw)))
    # 有支付时间用 paid_at，否则回落 created_at（未支付单）
    time_col = func.coalesce(Payment.paid_at, Payment.created_at)
    q = _apply_date_range(q, time_col, date_from, date_to)
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    result = await db.execute(q.order_by(Payment.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    rows = list(result.scalars())
    ids = [p.id for p in rows]
    order_ids = list({p.order_id for p in rows if p.order_id})
    order_map: dict[int, Order] = {}
    if order_ids:
        order_map = {
            o.id: o
            for o in (await db.execute(select(Order).where(Order.id.in_(order_ids)))).scalars().all()
        }
    store_ids = list({o.store_id for o in order_map.values() if o.store_id})
    store_map: dict[int, Store] = {}
    if store_ids:
        store_map = {
            s.id: s
            for s in (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars().all()
        }
    table_ids = list({o.table_id for o in order_map.values() if o.table_id})
    from app.models import DiningTable

    table_map: dict[int, DiningTable] = {}
    if table_ids:
        table_map = {
            t.id: t
            for t in (await db.execute(select(DiningTable).where(DiningTable.id.in_(table_ids)))).scalars().all()
        }

    refunded_map: dict[int, Decimal] = {}
    inflight_map: dict[int, Decimal] = {}
    if ids:
        success_rows = await db.execute(
            select(PaymentRefund.payment_id, func.coalesce(func.sum(PaymentRefund.amount), 0))
            .where(PaymentRefund.payment_id.in_(ids), PaymentRefund.status == "SUCCESS")
            .group_by(PaymentRefund.payment_id)
        )
        for pid, amt in success_rows.all():
            refunded_map[int(pid)] = Decimal(str(amt))
        inflight_rows = await db.execute(
            select(PaymentRefund.payment_id, func.coalesce(func.sum(PaymentRefund.amount), 0))
            .where(
                PaymentRefund.payment_id.in_(ids),
                PaymentRefund.status.in_(("PENDING", "PROCESSING")),
            )
            .group_by(PaymentRefund.payment_id)
        )
        for pid, amt in inflight_rows.all():
            inflight_map[int(pid)] = Decimal(str(amt))

    items = []
    for p in rows:
        refunded = refunded_map.get(p.id, Decimal("0"))
        inflight = inflight_map.get(p.id, Decimal("0"))
        remain = Decimal(str(p.amount)) - refunded - inflight
        if remain < 0:
            remain = Decimal("0")
        order = order_map.get(p.order_id)
        store = store_map.get(order.store_id) if order and order.store_id else None
        table = table_map.get(order.table_id) if order and order.table_id else None
        items.append(
            {
                "id": p.id,
                "payment_no": p.payment_no,
                "order_id": p.order_id,
                "order_no": order.order_no if order else None,
                "store_id": order.store_id if order else None,
                "store_name": store.name if store else None,
                "store_no": store.store_no if store else None,
                "table_id": order.table_id if order else None,
                "table_no": table.table_no if table else None,
                "table_name": table.table_name if table else None,
                "dining_type": order.dining_type if order else None,
                "order_channel": order.channel if order else None,
                "order_subtotal": str(order.subtotal) if order else None,
                "order_discount_amount": str(order.discount_amount) if order else None,
                "order_coupon_discount": str(order.coupon_discount) if order else None,
                "order_voucher_discount": str(order.voucher_discount) if order else None,
                "provider": p.provider,
                "channel": p.channel,
                "amount": str(p.amount),
                "refunded_amount": str(refunded),
                "refundable_amount": str(remain),
                "refund_inflight": str(inflight),
                "status": p.status,
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
            }
        )
    return success({"items": items, "total": total or 0, "page": page, "page_size": page_size}, request=request)


@router.get("/refunds", dependencies=[require_permission("payment:read")])
async def list_refunds(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = 1,
    page_size: int = 20,
    payment_no: str | None = None,
    status: str | None = None,
    store_id: int | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """退款单列表（运维：查处理中 / 按支付号钻取）。"""
    from app.models import Store, User
    from app.schemas.pagination import clamp_page

    page, page_size = clamp_page(page, page_size)
    q = (
        select(PaymentRefund)
        .join(Payment, Payment.id == PaymentRefund.payment_id)
        .join(Order, Order.id == PaymentRefund.order_id)
    )
    if payment_no:
        q = q.where(Payment.payment_no == payment_no.strip())
    if status:
        statuses = [s.strip().upper() for s in status.split(",") if s.strip()]
        if len(statuses) == 1:
            q = q.where(PaymentRefund.status == statuses[0])
        elif statuses:
            q = q.where(PaymentRefund.status.in_(statuses))
    effective_store = require_bound_store(user, store_id)
    if effective_store is not None:
        q = q.where(Order.store_id == effective_store)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = q.where(
            or_(
                PaymentRefund.refund_no.like(kw),
                Payment.payment_no.like(kw),
                Order.order_no.like(kw),
            )
        )
    q = _apply_date_range(q, PaymentRefund.created_at, date_from, date_to)

    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = list(
        (
            await db.execute(
                q.order_by(PaymentRefund.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars()
    )

    payment_ids = list({r.payment_id for r in rows})
    order_ids = list({r.order_id for r in rows})
    operator_ids = list({r.operator_id for r in rows if r.operator_id})

    pay_map: dict[int, Payment] = {}
    if payment_ids:
        pay_map = {
            p.id: p
            for p in (await db.execute(select(Payment).where(Payment.id.in_(payment_ids)))).scalars().all()
        }
    order_map: dict[int, Order] = {}
    if order_ids:
        order_map = {
            o.id: o
            for o in (await db.execute(select(Order).where(Order.id.in_(order_ids)))).scalars().all()
        }
    store_ids = list({o.store_id for o in order_map.values() if o.store_id})
    store_map: dict[int, Store] = {}
    if store_ids:
        store_map = {
            s.id: s
            for s in (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars().all()
        }
    op_map: dict[int, User] = {}
    if operator_ids:
        op_map = {
            u.id: u
            for u in (await db.execute(select(User).where(User.id.in_(operator_ids)))).scalars().all()
        }

    items = []
    for r in rows:
        payment = pay_map.get(r.payment_id)
        order = order_map.get(r.order_id)
        store = store_map.get(order.store_id) if order and order.store_id else None
        op = op_map.get(r.operator_id) if r.operator_id else None
        items.append(
            {
                "id": r.id,
                "refund_no": r.refund_no,
                "payment_id": r.payment_id,
                "payment_no": payment.payment_no if payment else None,
                "order_id": r.order_id,
                "order_no": order.order_no if order else None,
                "store_id": order.store_id if order else None,
                "store_name": store.name if store else None,
                "provider": payment.provider if payment else None,
                "amount": str(r.amount),
                "reason": r.reason,
                "status": r.status,
                "provider_refund_no": r.provider_refund_no,
                "fail_reason": r.fail_reason,
                "operator_id": r.operator_id,
                "operator_name": (op.display_name or op.username) if op else None,
                "succeeded_at": r.succeeded_at.isoformat() if r.succeeded_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
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


@router.get("/{payment_no}")
async def get_payment(payment_no: str, request: Request, db: DbSession, auth: StaffOrCustomer):
    user, customer = auth
    result = await db.execute(select(Payment).where(Payment.payment_no == payment_no))
    payment = result.scalar_one_or_none()
    if not payment:
        return success(None, message="支付记录不存在", request=request)
    if customer and payment.customer_id != customer.id:
        raise HTTPException(status_code=403, detail="无权查看此支付")
    if user is not None:
        order = await db.get(Order, payment.order_id)
        if order:
            assert_order_store_access(user, order)
    refunds = list(
        (
            await db.execute(
                select(PaymentRefund)
                .where(PaymentRefund.payment_id == payment.id)
                .order_by(PaymentRefund.id.desc())
            )
        ).scalars()
    )
    return success(
        {
            "payment_no": payment.payment_no,
            "status": payment.status,
            "amount": str(payment.amount),
            "provider": payment.provider,
            "refunds": [
                {
                    "refund_no": r.refund_no,
                    "amount": str(r.amount),
                    "status": r.status,
                    "reason": r.reason,
                    "fail_reason": r.fail_reason,
                    "provider_refund_no": r.provider_refund_no,
                    "operator_id": r.operator_id,
                    "succeeded_at": r.succeeded_at.isoformat() if r.succeeded_at else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in refunds
            ],
        },
        request=request,
    )

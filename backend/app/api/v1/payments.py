from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, StaffOrCustomer, require_permission
from app.core.response import calc_pages, success
from app.models import Order, Payment, PaymentRefund
from app.schemas.order import PaymentCreate
from app.services.payment_service import PaymentService
from app.services.store_scope import assert_order_store_access, require_bound_store
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/payments", tags=["支付"])


class RefundCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    reason: str = Field(default="商家退款", max_length=200)


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
    if order.customer_id and order.customer_id != customer.id:
        raise HTTPException(status_code=403, detail="无权支付此订单")
    payment, pay_data = await PaymentService.create_payment(
        db, order, data.provider, data.channel_type, customer.id
    )
    if pay_data.get("mock_settled"):
        await ws_manager.notify_order_event(
            "ORDER_PAID",
            {"order_id": order.id, "order_no": order.order_no, "store_id": order.store_id},
        )
    return success({"payment_no": payment.payment_no, "amount": str(payment.amount), **pay_data}, request=request)


@router.post("/{payment_no}/refund", dependencies=[require_permission("payment:refund")])
async def refund_payment(
    payment_no: str,
    data: RefundCreate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    result = await db.execute(select(Payment).where(Payment.payment_no == payment_no))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="支付记录不存在")
    order = await db.get(Order, payment.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    assert_order_store_access(user, order)
    row = await PaymentService.refund(db, payment, data.amount, data.reason, operator_id=user.id)
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
):
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
        items.append(
            {
                "id": p.id,
                "payment_no": p.payment_no,
                "order_id": p.order_id,
                "order_no": order.order_no if order else None,
                "store_id": order.store_id if order else None,
                "store_name": store.name if store else None,
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

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, StaffOrCustomer, require_permission
from app.core.response import calc_pages, success
from app.models import Order, Payment
from app.schemas.order import PaymentCreate
from app.services.payment_service import PaymentService
from app.services.store_scope import assert_order_store_access
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
        {"refund_no": row.refund_no, "status": row.status, "amount": str(row.amount)},
        request=request,
    )


@router.post("/wechat/notify")
async def wechat_notify(request: Request, db: DbSession):
    body = await request.body()
    headers = dict(request.headers)
    payment = await PaymentService.handle_callback(db, "WECHAT", headers, body)
    if payment and payment.status == "PAID":
        order = await db.get(Order, payment.order_id)
        if order:
            await ws_manager.notify_order_event(
                "ORDER_PAID",
                {"order_id": order.id, "order_no": order.order_no, "store_id": order.store_id},
            )
    return {"code": "SUCCESS", "message": "成功"}


@router.post("/alipay/notify")
async def alipay_notify(request: Request, db: DbSession):
    body = await request.body()
    headers = dict(request.headers)
    payment = await PaymentService.handle_callback(db, "ALIPAY", headers, body)
    if payment and payment.status == "PAID":
        order = await db.get(Order, payment.order_id)
        if order:
            await ws_manager.notify_order_event(
                "ORDER_PAID",
                {"order_id": order.id, "order_no": order.order_no, "store_id": order.store_id},
            )
    return "success"


@router.get("", dependencies=[require_permission("payment:read")])
async def list_payments(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
):
    from app.schemas.pagination import clamp_page

    page, page_size = clamp_page(page, page_size)
    q = select(Payment)
    if status:
        q = q.where(Payment.status == status)
    if user.store_id and user.role.code != "SUPER_ADMIN":
        q = q.join(Order, Order.id == Payment.order_id).where(Order.store_id == user.store_id)
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    result = await db.execute(q.order_by(Payment.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    items = [
        {
            "id": p.id,
            "payment_no": p.payment_no,
            "order_id": p.order_id,
            "provider": p.provider,
            "channel": p.channel,
            "amount": str(p.amount),
            "status": p.status,
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        }
        for p in result.scalars()
    ]
    return success({"items": items, "total": total or 0, "page": page, "page_size": page_size}, request=request)


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
    return success(
        {"payment_no": payment.payment_no, "status": payment.status, "amount": str(payment.amount), "provider": payment.provider},
        request=request,
    )

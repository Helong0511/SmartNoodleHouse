import logging
from decimal import Decimal

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, StaffOrCustomer, require_permission, require_pos
from app.core.exceptions import AppException
from app.core.cache_keys import pay_sync_key
from app.core.redis_client import get_redis
from app.core.response import apply_date_range as _apply_date_range, calc_pages, success, serialize_dt
from app.models import Order, Payment, PaymentRefund
from app.schemas.order import PaymentCreate, PosCashPay, PosScanPay
from app.services.audit_service import AuditService
from app.services.customer_service import CustomerService
from app.services.payment_service import PaymentService
from app.services.store_scope import assert_order_store_access, require_bound_store
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/payments", tags=["支付"])

logger = logging.getLogger(__name__)


class RefundCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    reason: str = Field(default="商家退款", max_length=200)


async def _acquire_sync_cooldown(payment_id: int) -> None:
    """P1-13: per-payment 2s Redis cooldown (SET NX EX 2) to avoid flooding provider query API."""
    redis_client = await get_redis()
    acquired = await redis_client.set(pay_sync_key(payment_id), "1", nx=True, ex=2)
    if not acquired:
        raise AppException(42901, "查询过于频繁，请稍后重试", status_code=429)


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
        # P2-10：找不到资源应返回 404 而非 success(null)
        raise AppException(40404, "订单不存在")
    from app.services.order_access import assert_customer_can_pay_order, assert_dining_session_owner

    # 禁止顾客 claim 未绑定的 POS 订单；仅本人订单可支付
    assert_customer_can_pay_order(order, customer.id)
    await assert_dining_session_owner(db, order, customer.id, action="支付")
    extra: dict | None = None
    provider_u = data.provider.upper()
    channel_l = data.channel_type.lower()
    # P1-6：小程序支付须校验渠道身份——顾客须已绑定对应平台的 openid/buyer_id
    if provider_u == "WECHAT" and channel_l == "miniprogram":
        openid = await _wechat_openid_for_customer(db, customer.id)
        if not openid:
            raise AppException(40030, "当前账号未绑定微信，无法使用微信支付")
        extra = {"openid": openid}
    elif provider_u == "ALIPAY" and channel_l == "miniprogram":
        buyer_id = await _alipay_buyer_id_for_customer(db, customer.id)
        if not buyer_id:
            raise AppException(40030, "当前账号未绑定支付宝，无法使用支付宝支付")
        extra = {"buyer_id": buyer_id}
    payment, pay_data = await PaymentService.create_payment(
        db, order, data.provider, data.channel_type, customer.id, extra=extra
    )
    if pay_data.get("mock_settled"):
        await db.commit()
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
                pay_data["points_earned"] = CustomerService.points_from_amount(order.total_amount)
                pay_data["member_points"] = profile.get("member_points", 0)
                pay_data["member_level_name"] = profile.get("member_level_name")
    await AuditService.log(
        db,
        action="payment.pay",
        module="payment",
        user_id=None,
        target_type="order",
        target_id=int(order.id),
        detail={
            "order_id": int(order.id),
            "order_no": order.order_no,
            "payment_no": payment.payment_no,
            "amount": str(payment.amount),
            "provider": data.provider,
            "channel_type": data.channel_type,
            "store_id": int(order.store_id) if order.store_id else None,
            "customer_id": int(customer.id),
        },
        request=request,
        category=AuditService.CATEGORY_CUSTOMER,
        store_id=int(order.store_id) if order.store_id else None,
    )
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
        raise AppException(40404, "订单不存在", status_code=404)
    from app.services.order_access import assert_customer_owns_order, assert_dining_session_owner

    assert_customer_owns_order(order, customer.id, action="同步支付")
    await assert_dining_session_owner(db, order, customer.id, action="同步支付")
    # P1-13: per-payment Redis cooldown before querying provider
    pending = (
        await db.execute(
            select(Payment)
            .where(Payment.order_id == order.id, Payment.status == "PENDING")
            .order_by(Payment.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if pending:
        await _acquire_sync_cooldown(pending.id)
    payment, meta = await PaymentService.sync_order_pending_payment(
        db, order, provider=provider
    )
    if meta.get("synced") and payment and payment.status == "PAID":
        await _notify_paid_or_refunded(db, payment, transitioned=True)
    await AuditService.log(
        db,
        action="payment.sync",
        module="payment",
        user_id=None,
        target_type="order",
        target_id=int(order.id),
        detail={
            "order_id": int(order.id),
            "order_no": order.order_no,
            "payment_no": payment.payment_no if payment else meta.get("payment_no"),
            "store_id": int(order.store_id) if order.store_id else None,
            "customer_id": int(customer.id),
            "synced": bool(meta.get("synced")),
        },
        request=request,
        category=AuditService.CATEGORY_CUSTOMER,
        store_id=int(order.store_id) if order.store_id else None,
    )
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
        raise AppException(40490, "支付记录不存在", status_code=404)
    order = await db.get(Order, payment.order_id)
    if customer:
        from app.services.order_access import assert_customer_owns_order, assert_dining_session_owner

        if not order:
            raise AppException(40404, "订单不存在", status_code=404)
        assert_customer_owns_order(order, customer.id, action="同步支付")
        await assert_dining_session_owner(db, order, customer.id, action="同步支付")
        if payment.customer_id is None or int(payment.customer_id) != int(customer.id):
            raise AppException(40301, "无权同步此支付", status_code=403)
    if user is not None and order:
        from app.api.deps import get_user_permissions

        perms = await get_user_permissions(db, user.role_id)
        if user.role.code != "SUPER_ADMIN" and "payment:read" not in perms:
            raise AppException(40300, "缺少权限: payment:read", status_code=403)
        assert_order_store_access(user, order)
    # P1-13: per-payment Redis cooldown before querying provider
    await _acquire_sync_cooldown(payment.id)
    before = payment.status
    payment, meta = await PaymentService.sync_payment_status(db, payment)
    if before != "PAID" and payment.status == "PAID":
        await _notify_paid_or_refunded(db, payment, transitioned=True)
    progress = await PaymentService.payment_progress(db, order) if order else {}
    audit_store_id = int(order.store_id) if order and order.store_id else None
    if user is not None:
        await AuditService.log_staff(
            db,
            user=user,
            action="payment.sync",
            module="payment",
            target_type="payment",
            target_id=int(payment.id),
            detail={
                "payment_no": payment.payment_no,
                "order_id": int(order.id) if order else None,
                "amount": str(payment.amount),
                "store_id": audit_store_id,
                "before": before,
                "after": payment.status,
            },
            request=request,
            store_id=audit_store_id,
        )
    else:
        await AuditService.log(
            db,
            action="payment.sync",
            module="payment",
            user_id=None,
            target_type="payment",
            target_id=int(payment.id),
            detail={
                "payment_no": payment.payment_no,
                "order_id": int(order.id) if order else None,
                "amount": str(payment.amount),
                "store_id": audit_store_id,
                "customer_id": int(customer.id) if customer else None,
                "before": before,
                "after": payment.status,
            },
            request=request,
            category=AuditService.CATEGORY_CUSTOMER,
            store_id=audit_store_id,
        )
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
    from app.services.pos_shift_service import PosShiftService

    body = data or PosCashPay()
    order = await db.get(Order, order_id)
    if not order:
        raise AppException(40404, "订单不存在", status_code=404)
    assert_order_store_access(user, order)
    await PosShiftService.require_open_shift(db, user.id, store_id=order.store_id)
    payment, pay_data = await PaymentService.settle_cash_payment(
        db,
        order,
        operator_id=user.id,
        amount=body.amount,
        received_amount=body.received_amount,
        change_amount=None,
        round_down=body.round_down,
    )
    if pay_data.get("fully_paid"):
        await db.commit()
        await ws_manager.notify_order_event(
            "ORDER_PAID",
            {"order_id": order.id, "order_no": order.order_no, "store_id": order.store_id, "channel": order.channel},
        )
    # amount / 进度字段放最后，避免被 pay_data 内同名键覆盖
    await AuditService.log_staff(
        db,
        user=user,
        action="payment.cash",
        module="payment",
        target_type="payment",
        target_id=int(payment.id),
        detail={
            "order_id": int(order.id),
            "order_no": order.order_no,
            "payment_no": payment.payment_no,
            "amount": str(payment.amount),
            "received_amount": str(body.received_amount) if body.received_amount is not None else None,
            "store_id": int(order.store_id) if order.store_id else None,
        },
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
    )
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
    from app.services.pos_shift_service import PosShiftService

    auth_code = (data.auth_code or "").strip()
    provider = resolve_barcode_provider(auth_code, data.provider)
    order = await db.get(Order, order_id)
    if not order:
        raise AppException(40404, "订单不存在", status_code=404)
    assert_order_store_access(user, order)
    await PosShiftService.require_open_shift(db, user.id, store_id=order.store_id)
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
        await db.commit()
        await ws_manager.notify_order_event(
            "ORDER_PAID",
            {
                "order_id": order.id,
                "order_no": order.order_no,
                "store_id": order.store_id,
                "channel": order.channel,
            },
        )
    await AuditService.log_staff(
        db,
        user=user,
        action="payment.pos_scan",
        module="payment",
        target_type="payment",
        target_id=int(payment.id),
        detail={
            "order_id": int(order.id),
            "order_no": order.order_no,
            "payment_no": payment.payment_no,
            "amount": str(payment.amount),
            "provider": provider,
            "store_id": int(order.store_id) if order.store_id else None,
        },
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
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


@router.post("/{payment_no}/refund", dependencies=[require_permission("payment:refund")])
async def refund_payment(
    payment_no: str,
    data: RefundCreate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=64),
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
        raise AppException(40490, "支付记录不存在", status_code=404)
    order = await db.get(Order, payment.order_id)
    if not order:
        raise AppException(40404, "订单不存在", status_code=404)
    if (order.channel or "").upper() == "POS" and user.role.code not in ("SUPER_ADMIN", "STORE_ADMIN"):
        raise AppException(40301, "收银渠道订单请走退款审批（店长/管理员执行）")
    assert_order_store_access(user, order)
    row = await PaymentService.refund(
        db,
        payment,
        data.amount,
        data.reason,
        operator_id=user.id,
        idempotency_key=idempotency_key,
    )
    # 幂等重放只返回原退款状态；不得重复写审计记录或重复广播成功事件。
    is_replay = bool(getattr(row, "_idempotent_replay", False))
    if not is_replay:
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
            store_id=order.store_id,
        )
        if row.status == "SUCCESS":
            await db.commit()
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
                else ("退款异常，需在支付平台处理后重新查询" if row.status == "ABNORMAL" else "退款已受理，等待平台确认")
                if row.status in ("PENDING", "PROCESSING", "UNKNOWN", "ABNORMAL")
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
        raise AppException(40492, "退款单不存在", status_code=404)
    order = await db.get(Order, row.order_id)
    if order:
        assert_order_store_access(user, order)
    before_status = row.status
    row = await PaymentService.sync_refund_status(db, row)
    # Persist the authoritative refund state before publishing the real-time event.
    # DbSession commits at dependency teardown, which happens after this endpoint returns;
    # notifying first can make clients observe SUCCESS while a concurrent read still sees
    # the previous state (or miss the event if the transaction later rolls back).
    await db.commit()
    if before_status != "SUCCESS" and row.status == "SUCCESS" and order:
        try:
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
        except Exception:
            # The refund transaction is already committed; a transient websocket failure
            # must not turn a successful refund sync into an API error.
            logger.exception("退款查单已提交，但 WebSocket 通知失败 refund_no=%s", row.refund_no)
    return success(
        {"refund_no": row.refund_no, "status": row.status, "amount": str(row.amount)},
        request=request,
    )


async def _notify_paid_or_refunded(db: DbSession, result, *, transitioned: bool = False) -> None:
    from app.models import PaymentRefund as PR

    if (
        isinstance(result, PR)
        and result.status == "SUCCESS"
        and (transitioned or bool(getattr(result, "_callback_transitioned_to_success", False)))
    ):
        order = await db.get(Order, result.order_id)
        if order:
            try:
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
            except Exception:
                # 数据库已提交；WebSocket 是可重取的实时提示，不能因此让支付平台重试已入账回调。
                logger.exception("退款回调已提交，但 WebSocket 通知失败 order_id=%s", order.id)
    elif (
        isinstance(result, Payment)
        and result.status == "PAID"
        and (transitioned or bool(getattr(result, "_callback_transitioned_to_paid", False)))
    ):
        order = await db.get(Order, result.order_id)
        if order:
            try:
                await ws_manager.notify_order_event(
                    "ORDER_PAID",
                    {"order_id": order.id, "order_no": order.order_no, "store_id": order.store_id},
                )
            except Exception:
                logger.exception("支付回调已提交，但 WebSocket 通知失败 order_id=%s", order.id)


@router.post("/wechat/notify")
async def wechat_notify(request: Request, db: DbSession):
    """支付与退款共用入口（按 resource / event_type 分流）。"""
    body = await request.body()
    headers = dict(request.headers)
    result = await PaymentService.handle_callback(db, "WECHAT", headers, body)
    # FastAPI yield 依赖默认在响应发送后执行；Webhook 必须先提交事实数据，再向微信 ACK。
    await db.commit()
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
    # 必须在返回支付宝 success 前提交，避免平台停止重试而本地事务随后回滚。
    await db.commit()
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
    operator_id: int | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """支付列表。keyword 匹配支付号/订单号；date_* 按支付时间 paid_at（空则 created_at）。"""
    from decimal import Decimal

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
    if operator_id:
        q = q.where(Payment.operator_id == int(operator_id))
    if amount_min is not None:
        q = q.where(Payment.amount >= Decimal(str(amount_min)))
    if amount_max is not None:
        q = q.where(Payment.amount <= Decimal(str(amount_max)))
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = q.where(or_(Payment.payment_no.like(kw), Order.order_no.like(kw)))
    # 有支付时间用 paid_at，否则回落 created_at（未支付单）
    time_col = func.coalesce(Payment.paid_at, Payment.created_at)
    q = _apply_date_range(q, time_col, date_from, date_to)
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    # 排序口径须与日期筛选一致（coalesce(paid_at, created_at)），并以 id 兜底保证翻页稳定
    result = await db.execute(
        q.order_by(time_col.desc(), Payment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
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

    from app.models import User

    op_ids = list({p.operator_id for p in rows if p.operator_id})
    op_map: dict[int, User] = {}
    if op_ids:
        op_map = {
            u.id: u
            for u in (await db.execute(select(User).where(User.id.in_(op_ids)))).scalars().all()
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
                PaymentRefund.status.in_(("PENDING", "PROCESSING", "UNKNOWN", "ABNORMAL")),
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
        op = op_map.get(p.operator_id) if p.operator_id else None
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
                "operator_id": p.operator_id,
                "operator_name": (op.display_name or op.username) if op else None,
                "paid_at": serialize_dt(p.paid_at),
            }
        )
    return success(
        {
            "items": items,
            "total": total or 0,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total or 0, page_size),
        },
        request=request,
    )


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
                "succeeded_at": serialize_dt(r.succeeded_at),
                "created_at": serialize_dt(r.created_at),
                "updated_at": serialize_dt(r.updated_at),
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
        raise AppException(40490, "支付记录不存在", status_code=404)
    if customer and payment.customer_id != customer.id:
        raise AppException(40301, "无权查看此支付", status_code=403)
    if user is not None:
        from app.api.deps import get_user_permissions

        perms = await get_user_permissions(db, user.role_id)
        if user.role.code != "SUPER_ADMIN" and "payment:read" not in perms:
            raise AppException(40300, "缺少权限: payment:read", status_code=403)
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
    from app.core.response import success_no_store

    return success_no_store(
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
                    "succeeded_at": serialize_dt(r.succeeded_at),
                    "created_at": serialize_dt(r.created_at),
                }
                for r in refunds
            ],
        },
        request=request,
    )

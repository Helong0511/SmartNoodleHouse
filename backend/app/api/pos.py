"""收银班次、日结与收银员统计 API。"""
from decimal import Decimal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, require_pos, require_settlement_manage
from app.core.exceptions import AppException
from app.core.response import success
from app.services.audit_service import AuditService
from app.services.customer_service import CustomerService
from app.services.pos_refund_service import PosRefundService
from app.services.pos_shift_service import PosShiftService
from app.services.store_scope import require_bound_store
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/pos", tags=["收银"])


class ShiftOpen(BaseModel):
    opening_float: Decimal = Field(default=Decimal("0"), ge=0, description="开班备用金")
    store_id: int | None = None


class ShiftClose(BaseModel):
    closing_cash: Decimal | None = Field(default=None, ge=0, description="交班实盘现金（含备用金）")
    remark: str | None = Field(default=None, max_length=500)


class PosRefundApply(BaseModel):
    order_id: int
    amount: Decimal | None = Field(default=None, gt=0, description="不传则全额可退余额")
    reason: str = Field(min_length=1, max_length=255)


class PosRefundReview(BaseModel):
    remark: str | None = Field(default=None, max_length=500)


class RememberPhoneIn(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11, description="11 位手机号")
    store_id: int | None = None


@router.get("/customers/search", dependencies=[require_pos()])
async def search_pos_customers(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    keyword: str = Query(..., min_length=1, max_length=100, description="手机号/昵称/会员号"),
    store_id: int | None = None,
    page_size: int = 20,
):
    """收银台绑客：按本店口径搜索会员（手机号/昵称/编号）。"""
    sid = require_bound_store(user, store_id)
    if sid is None:
        raise AppException(40008, "请选择门店")
    rows, _total = await CustomerService.search_active_customers(
        db, keyword, page=1, page_size=page_size, store_id=int(sid)
    )
    items = [
        {
            "id": c.id,
            "customer_no": c.customer_no,
            **CustomerService.identity_name_payload(c),
            "phone": c.phone,
            "is_member": CustomerService.is_member(c),
            "phone_verified": bool(int(getattr(c, "phone_verified", 0) or 0)),
            "is_phone_shell": bool(
                (c.display_name or "").startswith("收银预留")
                or (c.nickname or "").startswith("收银预留")
            ),
        }
        for c in rows
    ]
    return success({"items": items, "store_id": int(sid)}, request=request)


@router.post("/customers/remember-phone", dependencies=[require_pos()])
async def remember_pos_customer_phone(
    data: RememberPhoneIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """未找到会员时预留手机号：建/复用壳账号，便于本单计积分与当场公益核验。

    顾客日后小程序扫码并用官方授权绑定同一手机号时，积分、订单、公益身份会并入扫码账号。
    """
    sid = require_bound_store(user, data.store_id)
    if sid is None:
        raise AppException(40008, "请选择门店")
    customer, created = await CustomerService.remember_pos_phone(
        db, data.phone, store_id=int(sid)
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="pos.customer.remember_phone",
        module="pos",
        target_type="customer",
        target_id=int(customer.id),
        detail={
            "store_id": int(sid),
            "customer_id": int(customer.id),
            "phone_tail": str(data.phone)[-4:],
            "created": bool(created),
        },
        request=request,
        store_id=int(sid),
    )
    await db.commit()
    return success(
        {
            "id": customer.id,
            "customer_no": customer.customer_no,
            **CustomerService.identity_name_payload(customer),
            "phone": customer.phone,
            "is_member": CustomerService.is_member(customer),
            "phone_verified": bool(int(getattr(customer, "phone_verified", 0) or 0)),
            "is_phone_shell": True,
            "created": bool(created),
            "store_id": int(sid),
        },
        message="已记住手机号，顾客扫码绑号后可自动合并积分与公益身份"
        if created
        else "已找到该手机号对应顾客",
        request=request,
    )


@router.get("/shifts/current", dependencies=[require_pos()])
async def current_shift(request: Request, db: DbSession, user: CurrentUser):
    """当前收银员进行中的班次。"""
    shift = await PosShiftService.get_open_shift(db, user.id)
    if not shift:
        return success({"shift": None}, request=request)
    payload = await PosShiftService.shift_payload(db, shift, live=True)
    return success({"shift": payload}, request=request)


@router.post("/shifts/open", dependencies=[require_pos()])
async def open_shift(data: ShiftOpen, request: Request, db: DbSession, user: CurrentUser):
    """开班并登记备用金。"""
    store_id = require_bound_store(user, data.store_id)
    if store_id is None:
        raise AppException(40008, "请先绑定或选择门店")
    shift = await PosShiftService.open_shift(
        db,
        store_id=int(store_id),
        operator_id=user.id,
        opening_float=data.opening_float,
    )
    payload = await PosShiftService.shift_payload(db, shift, live=True)
    await AuditService.log_staff(
        db,
        user=user,
        action="pos.shift.open",
        module="pos",
        target_type="shift",
        target_id=shift.id,
        detail={"store_id": int(store_id), "opening_float": str(data.opening_float)},
        request=request,
        store_id=int(store_id),
    )
    return success(payload, request=request)


@router.post("/shifts/close", dependencies=[require_pos()])
async def close_my_shift(data: ShiftClose, request: Request, db: DbSession, user: CurrentUser):
    """收银员交班。"""
    shift = await PosShiftService.get_open_shift(db, user.id)
    if not shift:
        raise AppException(40404, "没有进行中的班次")
    shift = await PosShiftService.close_shift(
        db,
        shift,
        closing_cash=data.closing_cash,
        remark=data.remark,
        closed_by=user.id,
    )
    payload = await PosShiftService.shift_payload(db, shift)
    await AuditService.log_staff(
        db,
        user=user,
        action="pos.shift.close",
        module="pos",
        target_type="shift",
        target_id=shift.id,
        detail={
            "store_id": int(shift.store_id) if getattr(shift, "store_id", None) else None,
            "closing_cash": str(data.closing_cash) if data.closing_cash is not None else None,
            "remark": data.remark,
        },
        request=request,
        store_id=int(shift.store_id) if getattr(shift, "store_id", None) else None,
    )
    return success(payload, request=request)


@router.get("/shifts/current/orders", dependencies=[require_pos()])
async def current_shift_orders(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = 1,
    page_size: int = 50,
):
    """当前班次内本人收款的收银订单列表。"""
    shift = await PosShiftService.get_open_shift(db, user.id)
    if not shift:
        raise AppException(40404, "没有进行中的班次")
    data = await PosShiftService.list_shift_orders(db, shift, page=page, page_size=page_size)
    return success(data, request=request)


@router.get("/my-summary", dependencies=[require_pos()])
async def my_summary(request: Request, db: DbSession, user: CurrentUser):
    """收银员今日个人收款汇总（仅本人数据）。"""
    data = await PosShiftService.my_today_summary(db, user.id)
    return success(data, request=request)


@router.get("/settlement/daily", dependencies=[require_settlement_manage("仅店长或超管可查看日结报表")])
async def daily_settlement(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    date: str | None = None,
    store_id: int | None = None,
):
    """店长/超管：门店日结报表（各收银员班次、备用金、收款合计）。"""
    effective = require_bound_store(user, store_id)
    data = await PosShiftService.daily_settlement(db, store_id=effective, date_str=date)
    return success(data, request=request)


@router.post("/refund-requests", dependencies=[require_pos()])
async def apply_pos_refund(data: PosRefundApply, request: Request, db: DbSession, user: CurrentUser):
    """收银员申请退款（待店长/管理员审批）。"""
    from app.models import Order

    order = await db.get(Order, int(data.order_id))
    if not order:
        raise AppException(40402, "订单不存在")
    from app.services.store_scope import assert_order_store_access

    assert_order_store_access(user, order)
    await PosShiftService.require_open_shift(db, user.id, store_id=order.store_id)
    row = await PosRefundService.create_request(
        db,
        user=user,
        order_id=data.order_id,
        amount=data.amount,
        reason=data.reason,
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="pos.refund.apply",
        module="pos",
        target_type="refund_request",
        target_id=row.id,
        detail={
            "order_id": int(data.order_id),
            "amount": str(data.amount) if data.amount is not None else None,
            "reason": data.reason,
            "store_id": int(order.store_id) if order is not None else None,
        },
        request=request,
        store_id=int(order.store_id) if order is not None else None,
    )
    return success(await PosRefundService.payload(db, row), request=request)


@router.get("/refund-requests", dependencies=[require_pos()])
async def list_pos_refunds(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    status: str | None = None,
    store_id: int | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    """退款申请列表：收银员看本人；店长/管理员看本店。"""
    data = await PosRefundService.list_requests(
        db,
        user=user,
        status=status,
        store_id=store_id,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return success(data, request=request)


@router.post("/refund-requests/{request_id}/cancel", dependencies=[require_pos()])
async def cancel_pos_refund(request_id: int, request: Request, db: DbSession, user: CurrentUser):
    """撤销待审批的退款申请。"""
    row = await PosRefundService.cancel_request(db, user=user, request_id=request_id)
    await AuditService.log_staff(
        db,
        user=user,
        action="pos.refund.cancel",
        module="pos",
        target_type="refund_request",
        target_id=int(request_id),
        detail={"status": row.status, "store_id": int(getattr(row, "store_id", 0) or 0) or None},
        request=request,
        store_id=int(getattr(row, "store_id", 0) or 0) or None,
    )
    return success(await PosRefundService.payload(db, row), request=request)


@router.post(
    "/refund-requests/{request_id}/reject",
    dependencies=[require_settlement_manage("仅店长或超管可审批退款")],
)
async def reject_pos_refund(
    request_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    data: PosRefundReview | None = None,
):
    """店长/管理员拒绝退款申请。"""
    body = data or PosRefundReview()
    row = await PosRefundService.reject_request(db, user=user, request_id=request_id, remark=body.remark)
    await AuditService.log_staff(
        db,
        user=user,
        action="pos.refund.reject",
        module="pos",
        target_type="refund_request",
        target_id=int(request_id),
        detail={"remark": body.remark, "status": row.status, "store_id": int(getattr(row, "store_id", 0) or 0) or None},
        request=request,
        store_id=int(getattr(row, "store_id", 0) or 0) or None,
    )
    return success(await PosRefundService.payload(db, row), request=request)


@router.post(
    "/refund-requests/{request_id}/approve",
    dependencies=[require_settlement_manage("仅店长或超管可审批退款")],
)
async def approve_pos_refund(
    request_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    data: PosRefundReview | None = None,
):
    """店长/管理员审批通过并执行退款。"""
    body = data or PosRefundReview()
    row = await PosRefundService.approve_and_execute(
        db, user=user, request_id=request_id, remark=body.remark
    )
    audit_store_id: int | None = None
    if row.status == "SUCCESS":
        from app.models import Order

        order = await db.get(Order, row.order_id)
        if order:
            audit_store_id = int(order.store_id) if order.store_id else None
            await ws_manager.notify_order_event(
                "ORDER_REFUNDED",
                {
                    "order_id": order.id,
                    "order_no": order.order_no,
                    "amount": str(row.amount),
                    "store_id": order.store_id,
                    "pos_refund_request_id": row.id,
                },
            )
    await AuditService.log_staff(
        db,
        user=user,
        action="pos.refund.approve",
        module="pos",
        target_type="refund_request",
        target_id=int(request_id),
        detail={
            "remark": body.remark,
            "status": row.status,
            "amount": str(row.amount),
            "order_id": int(getattr(row, "order_id", 0) or 0) or None,
            "store_id": audit_store_id,
        },
        request=request,
        store_id=audit_store_id,
    )
    return success(await PosRefundService.payload(db, row), request=request)

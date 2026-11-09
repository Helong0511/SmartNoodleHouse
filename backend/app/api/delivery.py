"""美团 / 饿了么外卖接入 API"""
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import success, serialize_dt
from app.models import DeliveryCallbackLog, DeliveryOrder, DeliveryRefund
from app.services.audit_service import AuditService
from app.services.delivery_service import DeliveryService
from app.services.websocket_manager import ws_manager
from app.services.outbox_worker import enqueue_outbox, process_outbox_item
from app.services.webhook_inbox import _build_deferred_outbox_payload

router = APIRouter(prefix="/delivery", tags=["外卖"])

# 兼容测试旧导入路径
from app.api.delivery_sync import change_store_open_status as _change_store_open_status  # noqa: F401


class RefundAction(BaseModel):
    action: Literal["approve", "reject"]
    reason: str | None = None


class MerchantCancel(BaseModel):
    reason: str = "商家取消"


class RefundReconcile(BaseModel):
    action: Literal["platform_succeeded", "platform_not_applied"]
    reason: str = Field(..., min_length=8, max_length=500)


def _ws_payload(result, platform: str) -> dict:
    order = result.order
    data = {
        "channel": platform,
        "event": result.event,
        **result.extra,
    }
    if order:
        data.update({"order_id": order.id, "order_no": order.order_no, "status": order.status})
    return data


@router.post("/meituan/notify")
async def meituan_notify(request: Request, db: DbSession, background_tasks: BackgroundTasks):
    """美团推送：验签 → Inbox → 快速返回 ok；业务异步消费。"""
    from app.services.delivery_providers import get_delivery_provider
    from app.core.config import get_settings
    from app.services.webhook_inbox import enqueue_delivery_webhook, process_inbox_item

    body = await request.body()
    headers = dict(request.headers)
    settings = get_settings()
    try:
        provider = get_delivery_provider("MEITUAN")
        raw = provider.verify_notify(headers, body, notify_url=settings.meituan_notify_url)
    except ValueError as exc:
        import logging

        message = str(exc)
        status = 503 if ("未配置" in message or "无法验签" in message) else 401
        logging.getLogger("app.delivery").warning("美团回调验签/配置失败: %s", message)
        return JSONResponse({"data": "ng"}, status_code=status)
    except Exception:
        import logging

        logging.getLogger("app.delivery").exception("美团回调验签阶段异常")
        return JSONResponse({"data": "ng"}, status_code=503)

    try:
        row, is_new = await enqueue_delivery_webhook(
            db, platform="MEITUAN", headers=headers, body=body, raw=raw
        )
        await db.commit()
    except Exception:
        await db.rollback()
        import logging

        logging.getLogger("app.delivery").exception("美团回调持久化失败，要求平台重试")
        return JSONResponse({"data": "ng"}, status_code=503)
    if is_new:
        background_tasks.add_task(process_inbox_item, row.id)
    return JSONResponse({"data": "ok"})


@router.post("/eleme/notify")
async def eleme_notify(request: Request, db: DbSession, background_tasks: BackgroundTasks):
    """饿了么推送：验签 → Inbox → 快速返回 ok。"""
    from app.services.delivery_providers import get_delivery_provider
    from app.core.config import get_settings
    from app.services.webhook_inbox import enqueue_delivery_webhook, process_inbox_item

    body = await request.body()
    headers = dict(request.headers)
    settings = get_settings()
    try:
        provider = get_delivery_provider("ELEME")
        raw = provider.verify_notify(headers, body, notify_url=settings.eleme_notify_url)
    except ValueError as exc:
        import logging

        message = str(exc)
        status = 503 if ("未配置" in message or "无法验签" in message) else 401
        logging.getLogger("app.delivery").warning("饿了么回调验签/配置失败: %s", message)
        return JSONResponse({"message": "error"}, status_code=status)
    except Exception:
        import logging

        logging.getLogger("app.delivery").exception("饿了么回调验签阶段异常")
        return JSONResponse({"message": "error"}, status_code=503)

    try:
        row, is_new = await enqueue_delivery_webhook(
            db, platform="ELEME", headers=headers, body=body, raw=raw
        )
        await db.commit()
    except Exception:
        await db.rollback()
        import logging

        logging.getLogger("app.delivery").exception("饿了么回调持久化失败，要求平台重试")
        return JSONResponse({"message": "error"}, status_code=503)
    if is_new:
        background_tasks.add_task(process_inbox_item, row.id)
    return JSONResponse({"message": "ok"})


@router.post("/orders/{order_id}/confirm", dependencies=[require_permission("order:update")])
async def confirm_delivery_order(
    order_id: int, request: Request, db: DbSession, user: CurrentUser, background_tasks: BackgroundTasks
):
    from app.models import Order
    from app.services.store_scope import assert_order_store_access

    existing = await db.get(Order, order_id)
    if not existing:
        raise AppException(40404, "订单不存在", status_code=404)
    assert_order_store_access(user, existing)
    order, deferred = await DeliveryService.merchant_confirm(db, order_id)
    for task in deferred:
        outbox_row, _ = await enqueue_outbox(
            db,
            aggregate_type="delivery_manual",
            aggregate_id=int(order.id),
            event_kind=f"confirm_{uuid.uuid4().hex}",
            payload=_build_deferred_outbox_payload(task, task.get("platform") or ""),
        )
        # FastAPI function-scoped DB dependency commits before background tasks execute.
        background_tasks.add_task(process_outbox_item, outbox_row.id)
    await ws_manager.notify_order_event(
        "ORDER_CONFIRMED",
        {"order_id": order.id, "order_no": order.order_no, "status": order.status, "store_id": order.store_id},
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.order.confirm",
        module="delivery",
        target_type="order",
        target_id=int(order.id),
        detail={"order_id": int(order.id), "order_no": order.order_no, "status": order.status, "store_id": int(order.store_id) if order.store_id else None},
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
    )
    return success({"id": order.id, "status": order.status}, request=request)


@router.get("/stats", dependencies=[require_permission("order:read")])
async def delivery_stats(
    request: Request, db: DbSession, user: CurrentUser, store_id: int | None = None
):
    """外卖运营台顶部 KPI。"""
    from app.core.response import now_local
    from app.services.revenue_stats import delivery_kpi
    from app.services.store_scope import require_bound_store

    store = require_bound_store(user, store_id)
    today = now_local().replace(hour=0, minute=0, second=0, microsecond=0)
    return success(await delivery_kpi(db, since=today, store_id=store), request=request)


@router.get("/orders", dependencies=[require_permission("order:read")])
async def list_delivery_orders(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    platform: str | None = None,
    delivery_status: str | None = None,
    refund_status: str | None = None,
    keyword: str | None = None,
    day_seq: str | None = None,
    only_urge: bool = False,
    date_from: str | None = None,
    date_to: str | None = None,
    store_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
):
    from datetime import datetime

    from sqlalchemy import func, or_

    from app.core.response import calc_pages
    from app.models import Order
    from app.services.store_scope import require_bound_store

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = select(DeliveryOrder).join(Order, Order.id == DeliveryOrder.order_id)
    store = require_bound_store(user, store_id)
    if store:
        q = q.where(Order.store_id == store)
    if platform:
        q = q.where(DeliveryOrder.platform == platform.upper())
    if delivery_status:
        # 兼容旧前端 COMPLETED → DONE，CONFIRMED → ACCEPTED
        st = delivery_status.upper()
        if st == "COMPLETED":
            st = "DONE"
        if st == "CONFIRMED":
            st = "ACCEPTED"
        q = q.where(DeliveryOrder.delivery_status == st)
    if refund_status:
        q = q.where(DeliveryOrder.refund_status == refund_status.upper())
    if only_urge:
        q = q.where(DeliveryOrder.urge_count > 0, DeliveryOrder.delivery_status.notin_(["DONE", "CANCELLED"]))
    if day_seq:
        q = q.where(DeliveryOrder.platform_day_seq == str(day_seq).strip())
    if keyword:
        kw = f"%{keyword.strip()}%"
        q = q.where(
            or_(
                DeliveryOrder.platform_order_id.like(kw),
                DeliveryOrder.recipient_name.like(kw),
                DeliveryOrder.recipient_phone.like(kw),
                DeliveryOrder.recipient_address.like(kw),
                Order.order_no.like(kw),
            )
        )
    from app.core.response import apply_date_range

    q = apply_date_range(q, DeliveryOrder.created_at, date_from, date_to)

    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    result = await db.execute(
        q.order_by(DeliveryOrder.created_at.desc(), DeliveryOrder.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list(result.scalars().all())
    order_ids = [d.order_id for d in rows]
    order_map: dict[int, Order] = {}
    if order_ids:
        order_map = {
            o.id: o
            for o in (await db.execute(select(Order).where(Order.id.in_(order_ids)))).scalars().all()
        }
    store_ids = list({o.store_id for o in order_map.values() if o.store_id})
    from app.models import Store

    store_map: dict[int, Store] = {}
    if store_ids:
        store_map = {
            s.id: s
            for s in (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars().all()
        }

    items = []
    for d in rows:
        o = order_map.get(d.order_id)
        store = store_map.get(o.store_id) if o and o.store_id else None
        items.append(
            {
                "id": d.id,
                "order_id": d.order_id,
                "order_no": o.order_no if o else None,
                "store_id": o.store_id if o else None,
                "store_name": store.name if store else None,
                "store_no": store.store_no if store else None,
                "subtotal": str(o.subtotal) if o else None,
                "discount_amount": str(o.discount_amount) if o else None,
                "coupon_discount": str(o.coupon_discount) if o else None,
                "voucher_discount": str(o.voucher_discount) if o else None,
                "total_amount": str(o.total_amount) if o else None,
                "order_status": o.status if o else None,
                "remark": o.remark if o else None,
                "platform": d.platform,
                "platform_order_id": d.platform_order_id,
                "platform_day_seq": d.platform_day_seq,
                "delivery_status": d.delivery_status,
                "logistics_status": d.logistics_status,
                "logistics_code": d.logistics_code,
                "refund_status": d.refund_status,
                "urge_count": d.urge_count or 0,
                "recipient_name": d.recipient_name,
                "recipient_phone": d.recipient_phone,
                "recipient_address": d.recipient_address,
                "rider_name": d.rider_name,
                "rider_phone": d.rider_phone,
                "delivery_fee": str(d.delivery_fee or "0.00"),
                "packing_fee": str(d.packing_fee or "0.00"),
                "expected_delivery_at": serialize_dt(d.expected_delivery_at),
                "completed_at": serialize_dt(d.completed_at),
                "created_at": serialize_dt(d.created_at),
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


@router.get("/orders/{delivery_order_id}", dependencies=[require_permission("order:read")])
async def get_delivery_order(delivery_order_id: int, request: Request, db: DbSession, user: CurrentUser):
    from app.models import Order, OrderItem, OrderItemOption
    from app.services.store_scope import assert_order_store_access

    d = await db.get(DeliveryOrder, delivery_order_id)
    if not d:
        raise AppException(40405, "外卖订单不存在", status_code=404)
    order = await db.get(Order, d.order_id)
    if not order:
        raise AppException(40404, "订单不存在", status_code=404)
    assert_order_store_access(user, order)

    items_rows = list((await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))).scalars().all())
    item_ids = [i.id for i in items_rows]
    opts_by_item: dict[int, list] = {i: [] for i in item_ids}
    if item_ids:
        for opt in (await db.execute(select(OrderItemOption).where(OrderItemOption.order_item_id.in_(item_ids)))).scalars():
            opts_by_item.setdefault(opt.order_item_id, []).append(
                {
                    "group_name": opt.group_name_snapshot,
                    "option_name": opt.option_name_snapshot,
                    "extra_price": str(opt.extra_price_snapshot),
                }
            )
    items = [
        {
            "id": it.id,
            "product_name": it.product_name_snapshot,
            "unit_price": str(it.unit_price_snapshot),
            "quantity": it.quantity,
            "subtotal": str(it.subtotal),
            "remark": it.remark,
            "options": opts_by_item.get(it.id, []),
        }
        for it in items_rows
    ]
    return success(
        {
            "id": d.id,
            "order_id": d.order_id,
            "order_no": order.order_no,
            "subtotal": str(order.subtotal),
            "discount_amount": str(order.discount_amount),
            "coupon_discount": str(order.coupon_discount or "0.00"),
            "voucher_discount": str(order.voucher_discount or "0.00"),
            "total_amount": str(order.total_amount),
            "order_status": order.status,
            "remark": order.remark,
            "platform": d.platform,
            "platform_order_id": d.platform_order_id,
            "platform_day_seq": d.platform_day_seq,
            "delivery_status": d.delivery_status,
            "logistics_status": d.logistics_status,
            "logistics_code": d.logistics_code,
            "refund_status": d.refund_status,
            "urge_count": d.urge_count or 0,
            "recipient_name": d.recipient_name,
            "recipient_phone": d.recipient_phone,
            "recipient_address": d.recipient_address,
            "rider_name": d.rider_name,
            "rider_phone": d.rider_phone,
            "delivery_fee": str(d.delivery_fee or "0.00"),
            "packing_fee": str(d.packing_fee or "0.00"),
            "platform_fee": str(d.platform_fee or "0.00"),
            "expected_delivery_at": serialize_dt(d.expected_delivery_at),
            "completed_at": serialize_dt(d.completed_at),
            "created_at": serialize_dt(d.created_at),
            "items": items,
        },
        request=request,
    )


@router.get("/refunds", dependencies=[require_permission("order:read")])
async def list_refunds(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    status: str | None = None,
    store_id: int | None = None,
    platform: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    from datetime import datetime

    from sqlalchemy import or_

    from app.models import Order
    from app.services.store_scope import require_bound_store

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = (
        select(DeliveryRefund)
        .join(Order, Order.id == DeliveryRefund.order_id)
        .join(DeliveryOrder, DeliveryOrder.id == DeliveryRefund.delivery_order_id)
    )
    store = require_bound_store(user, store_id)
    if store:
        q = q.where(Order.store_id == store)
    if status:
        q = q.where(DeliveryRefund.status == status.upper())
    if platform:
        q = q.where(DeliveryRefund.platform == platform.strip().upper())
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = q.where(
            or_(
                DeliveryOrder.platform_order_id.like(kw),
                DeliveryRefund.platform_refund_id.like(kw),
                Order.order_no.like(kw),
            )
        )
    from app.core.response import apply_date_range, calc_pages
    from sqlalchemy import func

    q = apply_date_range(q, DeliveryRefund.created_at, date_from, date_to)

    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    result = await db.execute(
        q.order_by(DeliveryRefund.created_at.desc(), DeliveryRefund.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list(result.scalars().all())
    d_ids = list({r.delivery_order_id for r in rows})
    d_map: dict[int, DeliveryOrder] = {}
    if d_ids:
        d_map = {
            d.id: d
            for d in (await db.execute(select(DeliveryOrder).where(DeliveryOrder.id.in_(d_ids)))).scalars().all()
        }
    items = []
    for r in rows:
        d = d_map.get(r.delivery_order_id)
        items.append(
            {
                "id": r.id,
                "order_id": r.order_id,
                "delivery_order_id": r.delivery_order_id,
                "platform": r.platform,
                "platform_refund_id": r.platform_refund_id,
                "platform_order_id": d.platform_order_id if d else None,
                "platform_day_seq": d.platform_day_seq if d else None,
                "refund_type": r.refund_type,
                "amount": str(r.amount),
                "reason": r.reason,
                "status": r.status,
                "handled_at": serialize_dt(r.handled_at),
                "created_at": serialize_dt(r.created_at),
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


@router.post("/refunds/{refund_id}/action", dependencies=[require_permission("payment:refund")])
async def refund_action(refund_id: int, data: RefundAction, request: Request, db: DbSession, user: CurrentUser):
    from app.models import Order
    from app.services.store_scope import assert_order_store_access

    refund = await db.get(DeliveryRefund, refund_id)
    if not refund:
        raise AppException(40492, "退款单不存在", status_code=404)
    order = await db.get(Order, refund.order_id)
    if not order:
        raise AppException(40404, "订单不存在", status_code=404)
    assert_order_store_access(user, order)
    approve = data.action == "approve"
    result = await DeliveryService.process_refund(db, refund_id, approve, data.reason)
    await ws_manager.notify_order_event(
        "ORDER_REFUND_HANDLED",
        {"refund_id": refund_id, "action": data.action, "store_id": order.store_id, **result},
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.refund.action",
        module="delivery",
        target_type="refund",
        target_id=int(refund_id),
        detail={"action": data.action, "reason": data.reason, "order_id": int(order.id), "store_id": int(order.store_id) if order.store_id else None, **result},
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
    )
    return success(result, request=request)


@router.post("/refunds/{refund_id}/reconcile", dependencies=[require_permission("system:manage")])
async def reconcile_delivery_refund(
    refund_id: int,
    data: RefundReconcile,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """超级管理员核实 PROCESSING 退款的外部结果；不确定状态绝不自动重调平台。"""
    from app.models import Order

    if getattr(getattr(user, "role", None), "code", None) != "SUPER_ADMIN":
        raise AppException(40300, "退款结果核实仅超级管理员可操作", status_code=403)
    reason = data.reason.strip()
    required_phrase = (
        "平台核实已成功"
        if data.action == "platform_succeeded"
        else "平台核实未执行"
    )
    if required_phrase not in reason:
        raise AppException(
            40957,
            f"核实说明必须包含“{required_phrase}”，以明确人工核实结论",
            status_code=409,
        )

    hint = await db.get(DeliveryRefund, refund_id)
    if not hint:
        raise AppException(40492, "退款单不存在", status_code=404)
    order_id = int(hint.order_id)
    delivery_order_id = int(hint.delivery_order_id)

    # 与退款审批、商家取消、平台回调共用锁序：Order → DeliveryOrder → DeliveryRefund。
    order = (
        await db.execute(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if not order:
        raise AppException(40404, "关联订单不存在", status_code=404)
    d_order = (
        await db.execute(
            select(DeliveryOrder)
            .where(
                DeliveryOrder.id == delivery_order_id,
                DeliveryOrder.order_id == order_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if not d_order:
        raise AppException(40450, "关联外卖订单不存在", status_code=404)
    refund = (
        await db.execute(
            select(DeliveryRefund)
            .where(
                DeliveryRefund.id == int(refund_id),
                DeliveryRefund.order_id == order_id,
                DeliveryRefund.delivery_order_id == delivery_order_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if not refund:
        raise AppException(40492, "退款单不存在或关联关系已变化", status_code=404)
    if refund.status != "PROCESSING":
        raise AppException(40958, f"退款当前状态为 {refund.status}，不能进行结果核实", status_code=409)

    previous_status = refund.status
    if data.action == "platform_succeeded":
        # 先持久化“平台已退款，待补账”，然后复用 ledger-only 路径；绝不再调平台。
        refund.status = "APPROVED_LEDGER_PENDING"
        d_order.refund_status = "APPROVED_LEDGER_PENDING"
    else:
        # 平台已确认未执行：恢复待审核，后续由商家明确重新提交，不在本接口自动调用平台。
        refund.status = "PENDING"
        d_order.refund_status = "PENDING"
        refund.handled_at = None

    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.refund.reconcile",
        module="delivery",
        target_type="refund",
        target_id=int(refund_id),
        detail={
            "action": data.action,
            "previous_status": previous_status,
            "status_after_reconcile": refund.status,
            "reason": reason,
            "order_id": order_id,
            "store_id": int(order.store_id) if order.store_id else None,
        },
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
    )
    await db.commit()

    if data.action == "platform_succeeded":
        # 此状态分支只补财务分录与本地终态，不会请求美团/饿了么。
        result = await DeliveryService.process_refund(db, refund_id, approve=True)
        return success(
            {**result, "reconciled": True, "platform_call": False},
            request=request,
        )

    return success(
        {
            "ok": True,
            "status": "PENDING",
            "reconciled": True,
            "platform_call": False,
        },
        request=request,
    )


@router.post("/orders/{delivery_order_id}/cancel", dependencies=[require_permission("order:update")])
async def merchant_cancel_order(
    delivery_order_id: int,
    data: MerchantCancel,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    from app.models import Order
    from app.services.store_scope import assert_order_store_access

    d_order = await db.get(DeliveryOrder, delivery_order_id)
    if not d_order:
        raise AppException(40405, "外卖订单不存在", status_code=404)
    order = await db.get(Order, d_order.order_id)
    if not order:
        raise AppException(40404, "订单不存在", status_code=404)
    assert_order_store_access(user, order)
    result = await DeliveryService.cancel_by_merchant(db, delivery_order_id, data.reason)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.order.cancel",
        module="delivery",
        target_type="delivery_order",
        target_id=int(delivery_order_id),
        detail={"reason": data.reason, "order_id": int(order.id), "store_id": int(order.store_id) if order.store_id else None, **result},
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
    )
    return success(result, request=request)


@router.get("/callback-logs", dependencies=[require_permission("order:read")])
async def list_callback_logs(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    platform: str | None = None,
    event_type: str | None = None,
    store_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    from datetime import datetime

    from sqlalchemy import func

    from app.core.response import calc_pages
    from app.models import Order
    from app.services.store_scope import require_bound_store

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = select(DeliveryCallbackLog).outerjoin(Order, Order.id == DeliveryCallbackLog.order_id)
    # 门店隔离：门店账号只能看关联到本店订单的回调（孤立的平台级回调归超管排查）；
    # 超管传 store_id 看单店（不含孤立日志），不传看全部
    effective_store = require_bound_store(user, store_id)
    if effective_store is not None:
        q = q.where(Order.store_id == effective_store)
    if platform:
        q = q.where(DeliveryCallbackLog.platform == platform.upper())
    if event_type:
        q = q.where(DeliveryCallbackLog.event_type == event_type)
    from app.core.response import apply_date_range

    q = apply_date_range(q, DeliveryCallbackLog.created_at, date_from, date_to)
    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    # 事件类型为平台原始事件码（开放集合），返回当前过滤口径下去重值供前端联动下拉
    event_types = list(
        (
            await db.execute(
                q.with_only_columns(DeliveryCallbackLog.event_type)
                .where(DeliveryCallbackLog.event_type.isnot(None))
                .distinct()
                .order_by(DeliveryCallbackLog.event_type)
                .limit(100)
            )
        ).scalars().all()
    )
    result = await db.execute(
        q.order_by(DeliveryCallbackLog.created_at.desc(), DeliveryCallbackLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        {
            "id": log.id,
            "platform": log.platform,
            "event_type": log.event_type,
            "platform_order_id": log.platform_order_id,
            "order_id": log.order_id,
            "created_at": serialize_dt(log.created_at),
        }
        for log in result.scalars()
    ]
    return success(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
            "event_types": event_types,
        },
        request=request,
    )

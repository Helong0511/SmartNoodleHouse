"""美团 / 饿了么外卖接入 API"""
import asyncio
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import success, serialize_dt
from app.models import (
    DeliveryCallbackLog,
    DeliveryOrder,
    DeliveryProductMap,
    DeliveryRefund,
    DeliverySyncAlert,
    PlatformCategoryMap,
    PlatformStoreBinding,
    Product,
    ProductCategory,
)
from app.services.audit_service import AuditService
from app.services.delivery_events import EVENT_WS_MAP
from app.services.delivery_service import DeliveryService
from app.services.delivery_tasks import run_deferred_tasks
from app.services.product_sync_service import ProductSyncService
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/delivery", tags=["外卖"])


class ProductMapCreate(BaseModel):
    store_id: int | None = None
    platform: str = Field(description="MEITUAN 或 ELEME")
    platform_sku_id: str
    platform_spu_code: str | None = None
    app_food_code: str | None = None
    option_signature: str | None = None
    platform_sku_name: str | None = None
    product_id: int
    option_map_json: str | None = None


class StoreBindingCreate(BaseModel):
    store_id: int | None = None
    platform: str
    platform_shop_id: str
    platform_shop_name: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None


class RefundAction(BaseModel):
    action: Literal["approve", "reject"]
    reason: str | None = None


class MerchantCancel(BaseModel):
    reason: str = "商家取消"


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


async def _after_notify(result, platform: str, deferred: list):
    ws_event = EVENT_WS_MAP.get(result.event, "DELIVERY_UNKNOWN")
    await ws_manager.notify_order_event(ws_event, _ws_payload(result, platform))
    if deferred:
        asyncio.create_task(run_deferred_tasks(deferred))


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
        row, is_new = await enqueue_delivery_webhook(
            db, platform="MEITUAN", headers=headers, body=body, raw=raw
        )
        await db.commit()
        if is_new:
            background_tasks.add_task(process_inbox_item, row.id)
    except Exception as e:
        import logging

        logging.getLogger("app.delivery").warning("美团回调处理失败: %s", e, exc_info=True)
        return JSONResponse({"data": "ng"}, status_code=200)
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
        row, is_new = await enqueue_delivery_webhook(
            db, platform="ELEME", headers=headers, body=body, raw=raw
        )
        await db.commit()
        if is_new:
            background_tasks.add_task(process_inbox_item, row.id)
    except Exception as e:
        import logging

        logging.getLogger("app.delivery").warning("饿了么回调处理失败: %s", e, exc_info=True)
        return JSONResponse({"message": "error"}, status_code=200)
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
    if deferred:
        background_tasks.add_task(run_deferred_tasks, deferred)
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
    if result.get("order_id"):
        await ws_manager.notify_order_event(
            "ORDER_CANCELLED",
            {"order_id": result["order_id"], "reason": data.reason, "by": "merchant"},
        )
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


@router.get("/store-bindings", dependencies=[require_permission("product:read")])
async def list_store_bindings(request: Request, db: DbSession, user: CurrentUser, store_id: int | None = None):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        return success({"items": []}, request=request)
    assert_store_access(user, sid)
    result = await db.execute(
        select(PlatformStoreBinding).where(PlatformStoreBinding.store_id == sid).order_by(PlatformStoreBinding.platform)
    )
    items = [
        {
            "id": b.id,
            "store_id": b.store_id,
            "platform": b.platform,
            "platform_shop_id": b.platform_shop_id,
            "platform_shop_name": b.platform_shop_name,
            "status": b.status,
            "has_token": bool(b.access_token),
            "has_refresh_token": bool(b.refresh_token),
            "token_expires_at": serialize_dt(b.token_expires_at),
            "auto_sync_open": bool(b.auto_sync_open),
            "platform_open_status": b.platform_open_status,
        }
        for b in result.scalars()
    ]
    return success({"items": items}, request=request)


@router.post("/store-bindings", dependencies=[require_permission("product:update")])
async def upsert_store_binding(data: StoreBindingCreate, request: Request, db: DbSession, user: CurrentUser):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, data.store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise AppException(40008, "缺少门店", status_code=400)
    assert_store_access(user, sid)
    existing = (
        await db.execute(
            select(PlatformStoreBinding).where(
                PlatformStoreBinding.store_id == sid,
                PlatformStoreBinding.platform == data.platform.upper(),
                PlatformStoreBinding.platform_shop_id == data.platform_shop_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.platform_shop_name = data.platform_shop_name
        if data.access_token:
            from app.services.secret_encryption import SecretEncryptionService

            existing.access_token = SecretEncryptionService.encrypt(data.access_token)
        if data.refresh_token:
            from app.services.secret_encryption import SecretEncryptionService

            existing.refresh_token = SecretEncryptionService.encrypt(data.refresh_token)
        existing.status = 1
        await db.flush()
        await AuditService.log_staff(
            db,
            user=user,
            action="delivery.binding.create",
            module="delivery",
            target_type="platform_store_binding",
            target_id=int(existing.id),
            detail={"store_id": int(sid), "platform": data.platform.upper(), "platform_shop_id": data.platform_shop_id, "updated": True},
            request=request,
            store_id=int(sid) if sid else None,
        )
        return success({"id": existing.id, "updated": True}, request=request)

    from app.services.secret_encryption import SecretEncryptionService

    row = PlatformStoreBinding(
        store_id=sid,
        platform=data.platform.upper(),
        platform_shop_id=data.platform_shop_id,
        platform_shop_name=data.platform_shop_name,
        access_token=SecretEncryptionService.encrypt(data.access_token) if data.access_token else None,
        refresh_token=SecretEncryptionService.encrypt(data.refresh_token) if data.refresh_token else None,
    )
    db.add(row)
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.binding.create",
        module="delivery",
        target_type="platform_store_binding",
        target_id=int(row.id),
        detail={"store_id": int(sid), "platform": data.platform.upper(), "platform_shop_id": data.platform_shop_id, "updated": False},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success({"id": row.id, "updated": False}, request=request)


@router.get("/store-bindings/{binding_id}", dependencies=[require_permission("product:read")])
async def get_store_binding(binding_id: int, request: Request, db: DbSession, user: CurrentUser):
    """获取绑定详情（含 token 过期时间，便于管理后台展示）。"""
    from app.services.store_scope import assert_store_access

    row = await db.get(PlatformStoreBinding, binding_id)
    if not row:
        raise AppException(40450, "绑定不存在", status_code=404)
    assert_store_access(user, row.store_id)
    return success(
        {
            "id": row.id,
            "store_id": row.store_id,
            "platform": row.platform,
            "platform_shop_id": row.platform_shop_id,
            "platform_shop_name": row.platform_shop_name,
            "has_token": bool(row.access_token),
            "has_refresh_token": bool(row.refresh_token),
            "token_expires_at": serialize_dt(row.token_expires_at),
            "status": row.status,
            "updated_at": serialize_dt(row.updated_at),
        },
        request=request,
    )


@router.delete("/store-bindings/{binding_id}", dependencies=[require_permission("product:update")])
async def delete_store_binding(binding_id: int, request: Request, db: DbSession, user: CurrentUser):
    """解绑平台门店：清除 token 并停用，不真正删除行（保留审计/对账链路）。"""
    from app.services.store_scope import assert_store_access

    row = await db.get(PlatformStoreBinding, binding_id)
    if not row:
        raise AppException(40450, "绑定不存在", status_code=404)
    assert_store_access(user, row.store_id)
    row.access_token = None
    row.refresh_token = None
    row.token_expires_at = None
    row.status = 0
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.binding.delete",
        module="delivery",
        target_type="platform_store_binding",
        target_id=int(row.id),
        detail={"store_id": int(row.store_id) if row.store_id else None, "platform": row.platform, "platform_shop_id": row.platform_shop_id},
        request=request,
        store_id=int(row.store_id) if row.store_id else None,
    )
    return success({"id": row.id, "status": 0}, request=request)


class BindingStatusUpdate(BaseModel):
    status: int = Field(ge=0, le=1, description="1=启用, 0=停用")


@router.patch("/store-bindings/{binding_id}/status", dependencies=[require_permission("product:update")])
async def update_store_binding_status(
    binding_id: int, data: BindingStatusUpdate, request: Request, db: DbSession, user: CurrentUser
):
    """切换绑定启用/停用（停用后回调会被拒绝落单）。"""
    from app.services.store_scope import assert_store_access

    row = await db.get(PlatformStoreBinding, binding_id)
    if not row:
        raise AppException(40450, "绑定不存在", status_code=404)
    assert_store_access(user, row.store_id)
    row.status = int(data.status)
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.binding.status",
        module="delivery",
        target_type="platform_store_binding",
        target_id=int(row.id),
        detail={"store_id": int(row.store_id) if row.store_id else None, "status": int(row.status)},
        request=request,
        store_id=int(row.store_id) if row.store_id else None,
    )
    return success({"id": row.id, "status": row.status}, request=request)


@router.post("/store-bindings/{binding_id}/refresh-token", dependencies=[require_permission("product:update")])
async def refresh_store_binding_token(
    binding_id: int, request: Request, db: DbSession, user: CurrentUser
):
    """手动触发 Token 刷新（美团/饿了么均支持）。失败返回原因。"""
    from app.services.platform_token_service import PlatformTokenService
    from app.services.store_scope import assert_store_access

    row = await db.get(PlatformStoreBinding, binding_id)
    if not row:
        raise AppException(40450, "绑定不存在", status_code=404)
    assert_store_access(user, row.store_id)
    if not row.refresh_token:
        raise AppException(40051, "未保存 refresh_token，无法刷新", status_code=400)
    ok = await PlatformTokenService.refresh_single_binding(row)
    await db.commit()
    if not ok:
        raise AppException(50040, "刷新失败，请检查日志或重试", status_code=500)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.binding.refresh_token",
        module="delivery",
        target_type="platform_store_binding",
        target_id=int(row.id),
        detail={"store_id": int(row.store_id) if row.store_id else None, "platform": row.platform, "token_expires_at": serialize_dt(row.token_expires_at)},
        request=request,
        store_id=int(row.store_id) if row.store_id else None,
    )
    return success(
        {
            "id": row.id,
            "token_expires_at": serialize_dt(row.token_expires_at),
        },
        request=request,
    )


class SingleProductSyncPayload(BaseModel):
    store_id: int | None = None
    platform: str
    product_id: int


@router.post("/sync/menu", dependencies=[require_permission("product:update")])
async def sync_menu(
    request: Request, db: DbSession, user: CurrentUser, store_id: int | None = None, platform: str = "MEITUAN"
):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise AppException(40008, "缺少门店", status_code=400)
    assert_store_access(user, sid)
    result = await ProductSyncService.sync_store_menu(db, int(sid), platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.menu",
        module="delivery",
        target_type="store",
        target_id=int(sid),
        detail={"store_id": int(sid), "platform": platform, **result},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success(result, request=request)


@router.post("/sync/product", dependencies=[require_permission("product:update")])
async def sync_single_product(
    data: SingleProductSyncPayload, request: Request, db: DbSession, user: CurrentUser
):
    """推送单个餐品到指定外卖平台。"""
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, data.store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise AppException(40008, "缺少门店", status_code=400)
    assert_store_access(user, sid)
    result = await ProductSyncService.sync_single_product(db, int(sid), data.platform, data.product_id)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.product",
        module="delivery",
        target_type="product",
        target_id=int(data.product_id),
        detail={"store_id": int(sid), "platform": data.platform, "product_id": int(data.product_id), **result},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success(result, request=request)


@router.post("/sync/prices", dependencies=[require_permission("product:update")])
async def sync_prices(
    request: Request, db: DbSession, user: CurrentUser, store_id: int | None = None, platform: str = "MEITUAN"
):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise AppException(40008, "缺少门店", status_code=400)
    assert_store_access(user, sid)
    result = await ProductSyncService.sync_prices(db, int(sid), platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.prices",
        module="delivery",
        target_type="store",
        target_id=int(sid),
        detail={"store_id": int(sid), "platform": platform, **result},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success(result, request=request)


@router.post("/sync/stock", dependencies=[require_permission("product:update")])
async def sync_stock(
    request: Request, db: DbSession, user: CurrentUser, store_id: int | None = None, platform: str = "MEITUAN"
):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise AppException(40008, "缺少门店", status_code=400)
    assert_store_access(user, sid)
    result = await ProductSyncService.sync_stock(db, int(sid), platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.stock",
        module="delivery",
        target_type="store",
        target_id=int(sid),
        detail={"store_id": int(sid), "platform": platform, **result},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success(result, request=request)


@router.get("/product-maps", dependencies=[require_permission("product:read")])
async def list_product_maps(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    platform: str | None = None,
    store_id: int | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 100,
):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        return success(
            {"items": [], "total": 0, "page": 1, "page_size": page_size, "total_pages": 0},
            request=request,
        )
    assert_store_access(user, sid)
    store_id = int(sid)
    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 100)), 200)
    from sqlalchemy import func, or_

    from app.core.response import calc_pages

    q = select(DeliveryProductMap).where(DeliveryProductMap.store_id == store_id)
    if platform:
        q = q.where(DeliveryProductMap.platform == platform.upper())
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = q.where(
            or_(
                DeliveryProductMap.platform_sku_id.like(kw),
                DeliveryProductMap.platform_sku_name.like(kw),
                DeliveryProductMap.app_food_code.like(kw),
            )
        )
    total = int(await db.scalar(select(func.count()).select_from(q.subquery())) or 0)
    result = await db.execute(
        q.order_by(DeliveryProductMap.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    maps = list(result.scalars().all())
    product_ids = list({m.product_id for m in maps})
    products = {}
    if product_ids:
        products = {
            p.id: p
            for p in (await db.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()
        }
    items = [
        {
            "id": m.id,
            "platform": m.platform,
            "platform_sku_id": m.platform_sku_id,
            "platform_spu_code": m.platform_spu_code,
            "app_food_code": m.app_food_code,
            "option_signature": m.option_signature,
            "platform_sku_name": m.platform_sku_name,
            "product_id": m.product_id,
            "product_name": products[m.product_id].name if m.product_id in products else None,
            "status": m.status,
        }
        for m in maps
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


@router.post("/product-maps", dependencies=[require_permission("product:update")])
async def create_product_map(data: ProductMapCreate, request: Request, db: DbSession, user: CurrentUser):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, data.store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise AppException(40008, "缺少门店", status_code=400)
    assert_store_access(user, sid)
    product = await db.get(Product, data.product_id)
    if not product:
        return success(None, message="餐品不存在", request=request)
    if product.store_id is None or int(product.store_id) != int(sid):
        raise AppException(40301, "餐品不属于该门店，禁止映射", status_code=403)
    existing = await db.execute(
        select(DeliveryProductMap).where(
            DeliveryProductMap.store_id == sid,
            DeliveryProductMap.platform == data.platform.upper(),
            DeliveryProductMap.platform_sku_id == data.platform_sku_id,
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        row.product_id = data.product_id
        row.platform_sku_name = data.platform_sku_name
        row.platform_spu_code = data.platform_spu_code
        row.app_food_code = data.app_food_code or product.product_no
        row.option_signature = data.option_signature
        row.option_map_json = data.option_map_json
        row.status = 1
        await db.flush()
        await AuditService.log_staff(
            db,
            user=user,
            action="delivery.product_map.create",
            module="delivery",
            target_type="delivery_product_map",
            target_id=int(row.id),
            detail={"store_id": int(sid), "platform": data.platform.upper(), "platform_sku_id": data.platform_sku_id, "product_id": int(data.product_id), "updated": True},
            request=request,
            store_id=int(sid) if sid else None,
        )
        return success({"id": row.id, "updated": True}, request=request)

    row = DeliveryProductMap(
        store_id=sid,
        platform=data.platform.upper(),
        platform_sku_id=data.platform_sku_id,
        platform_spu_code=data.platform_spu_code,
        app_food_code=data.app_food_code or product.product_no,
        option_signature=data.option_signature,
        platform_sku_name=data.platform_sku_name,
        product_id=data.product_id,
        option_map_json=data.option_map_json,
    )
    db.add(row)
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.product_map.create",
        module="delivery",
        target_type="delivery_product_map",
        target_id=int(row.id),
        detail={"store_id": int(sid), "platform": data.platform.upper(), "platform_sku_id": data.platform_sku_id, "product_id": int(data.product_id), "updated": False},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success({"id": row.id, "updated": False}, request=request)


# ═════════════════════ 外卖管理重构：营业 / 菜单 / 监控 ═════════════════════

class HoursSlot(BaseModel):
    open_time: str = Field(description="HH:MM")
    close_time: str = Field(description="HH:MM")
    cross_day: bool = False


class HoursDay(BaseModel):
    weekday: int = Field(ge=1, le=7, description="1=周一 ... 7=周日")
    is_open: bool = True
    slots: list[HoursSlot] = []


class HoursPayload(BaseModel):
    days: list[HoursDay]


class AutoSyncOpenPayload(BaseModel):
    auto_sync_open: bool


class StoreOpenPayload(BaseModel):
    platform: str | None = None


class ShelfPayload(BaseModel):
    store_id: int
    platform: str
    on_shelf: bool
    product_ids: list[int] | None = None


class ReconcilePayload(BaseModel):
    store_id: int | None = None
    platform: str | None = None


class CategoryMapPayload(BaseModel):
    platform: str
    local_category_id: int
    platform_category_code: str | None = None
    platform_category_name: str | None = None
    store_id: int | None = None


@router.get("/stores/{store_id}/business-hours", dependencies=[require_permission("product:read")])
async def get_business_hours(store_id: int, request: Request, db: DbSession, user: CurrentUser):
    """读取门店一周营业时间。"""
    from app.services.store_hours_service import StoreHoursService
    from app.services.store_scope import assert_store_access

    assert_store_access(user, store_id)
    days = await StoreHoursService.get_hours_payload(db, store_id)
    return success({"store_id": store_id, "days": days}, request=request)


@router.put("/stores/{store_id}/business-hours", dependencies=[require_permission("product:update")])
async def set_business_hours(store_id: int, data: HoursPayload, request: Request, db: DbSession, user: CurrentUser):
    """全量覆盖门店一周营业时间（is_open=false 或无 slots = 当天休息）。"""
    from app.services.store_hours_service import StoreHoursService
    from app.services.store_scope import assert_store_access

    assert_store_access(user, store_id)
    try:
        days = await StoreHoursService.set_hours(db, store_id, [d.model_dump() for d in data.days])
    except ValueError as e:
        raise AppException(40000, str(e), status_code=400)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.business_hours.update",
        module="delivery",
        target_type="store",
        target_id=int(store_id),
        detail={"store_id": int(store_id), "days_count": len(data.days)},
        request=request,
        store_id=int(store_id),
    )
    return success({"store_id": store_id, "days": days}, request=request)


async def _change_store_open_status(db, user, store_id: int, open_status: str, platform: str | None):
    """手动开/关店：先组任务并 commit，再调平台，最后短事务写回（与自动开关店一致）。"""
    from app.core.response import naive_local_now
    from app.services.delivery_context import get_credentials_for_store
    from app.services.delivery_providers import get_delivery_provider
    from app.services.store_hours_service import StoreHoursService, _python_weekday, shipping_time_for_day
    from app.services.store_scope import assert_store_access

    assert_store_access(user, store_id)
    stmt = select(PlatformStoreBinding).where(
        PlatformStoreBinding.store_id == store_id,
        PlatformStoreBinding.status == 1,
    )
    if platform:
        stmt = stmt.where(PlatformStoreBinding.platform == platform.upper())
    bindings = list((await db.execute(stmt)).scalars().all())
    if not bindings:
        raise AppException(40050, "该门店没有已启用的平台绑定", status_code=400)

    now = naive_local_now()
    rows = await StoreHoursService.list_hours(db, store_id)
    shipping = shipping_time_for_day(rows, _python_weekday(now)) if open_status == "REST" else None

    jobs = []
    for b in bindings:
        creds = await get_credentials_for_store(db, b.platform, store_id)
        jobs.append((int(b.id), b.platform, creds))
    await db.commit()

    results = []
    for binding_id, plat, creds in jobs:
        try:
            provider = get_delivery_provider(plat, creds)
            ok = await provider.set_store_open_status(open_status, shipping)
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            row = await db.get(PlatformStoreBinding, binding_id)
            if row:
                row.platform_open_status = open_status
                row.last_sync_at = now
                await db.flush()
        results.append({"platform": plat, "ok": ok})
    await db.commit()
    return results


@router.post("/stores/{store_id}/open", dependencies=[require_permission("product:update")])
async def open_delivery_store(store_id: int, data: StoreOpenPayload, request: Request, db: DbSession, user: CurrentUser):
    """手动开店（平台上线营业）。"""
    results = await _change_store_open_status(db, user, store_id, "OPEN", data.platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.store.open",
        module="delivery",
        target_type="store",
        target_id=int(store_id),
        detail={"store_id": int(store_id), "platform": data.platform, "results": results},
        request=request,
        store_id=int(store_id),
    )
    return success({"store_id": store_id, "results": results}, request=request)


@router.post("/stores/{store_id}/close", dependencies=[require_permission("product:update")])
async def close_delivery_store(store_id: int, data: StoreOpenPayload, request: Request, db: DbSession, user: CurrentUser):
    """手动关店（商家休息/暂停接单）。"""
    results = await _change_store_open_status(db, user, store_id, "REST", data.platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.store.close",
        module="delivery",
        target_type="store",
        target_id=int(store_id),
        detail={"store_id": int(store_id), "platform": data.platform, "results": results},
        request=request,
        store_id=int(store_id),
    )
    return success({"store_id": store_id, "results": results}, request=request)


@router.patch(
    "/store-bindings/{binding_id}/auto-sync-open",
    dependencies=[require_permission("product:update")],
)
async def set_binding_auto_sync(
    binding_id: int, data: AutoSyncOpenPayload, request: Request, db: DbSession, user: CurrentUser
):
    """开启/关闭该绑定的按营业时间自动开/关店（默认关闭）。"""
    from app.services.store_scope import assert_store_access

    row = await db.get(PlatformStoreBinding, binding_id)
    if not row:
        raise AppException(40450, "绑定不存在", status_code=404)
    assert_store_access(user, row.store_id)
    row.auto_sync_open = bool(data.auto_sync_open)
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.binding.auto_sync_open",
        module="delivery",
        target_type="platform_store_binding",
        target_id=int(row.id),
        detail={"store_id": int(row.store_id) if row.store_id else None, "auto_sync_open": bool(row.auto_sync_open)},
        request=request,
        store_id=int(row.store_id) if row.store_id else None,
    )
    return success({"id": row.id, "auto_sync_open": row.auto_sync_open}, request=request)


@router.post("/sync/menu-batch", dependencies=[require_permission("product:update")])
async def sync_menu_batch(
    request: Request, db: DbSession, user: CurrentUser, store_id: int, platform: str = "MEITUAN"
):
    """批量推送门店菜单到平台（建档/更新），语义同 /sync/menu。"""
    from app.services.store_scope import assert_store_access

    assert_store_access(user, store_id)
    result = await ProductSyncService.sync_store_menu(db, store_id, platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.menu_batch",
        module="delivery",
        target_type="store",
        target_id=int(store_id),
        detail={"store_id": int(store_id), "platform": platform, **result},
        request=request,
        store_id=int(store_id),
    )
    return success(result, request=request)


@router.post("/sync/shelf", dependencies=[require_permission("product:update")])
async def sync_shelf(data: ShelfPayload, request: Request, db: DbSession, user: CurrentUser):
    """外卖平台侧批量上架/下架（不影响本地堂食）。"""
    from app.services.store_scope import assert_store_access

    assert_store_access(user, data.store_id)
    result = await ProductSyncService.set_shelf(
        db, data.store_id, data.platform, data.on_shelf, data.product_ids
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.shelf",
        module="delivery",
        target_type="store",
        target_id=int(data.store_id),
        detail={"store_id": int(data.store_id), "platform": data.platform, "on_shelf": bool(data.on_shelf), "product_ids": list(data.product_ids or []), **result},
        request=request,
        store_id=int(data.store_id),
    )
    return success(result, request=request)


@router.post("/sync/reconcile", dependencies=[require_permission("product:update")])
async def sync_reconcile(data: ReconcilePayload, request: Request, db: DbSession, user: CurrentUser):
    """手动触发库存对账：本地库存覆盖推送平台。"""
    from app.services.store_scope import assert_store_access, require_bound_store

    if user.role.code == "SUPER_ADMIN":
        sid = int(data.store_id) if data.store_id else None
        if sid is None:
            raise AppException(40061, "请指定要对账的门店")
        assert_store_access(user, sid)
    else:
        sid = require_bound_store(user, data.store_id)
        if sid is None:
            raise AppException(40301, "账号未绑定门店")
    result = await ProductSyncService.reconcile_stock(db, sid, data.platform)
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync.reconcile",
        module="delivery",
        target_type="store",
        target_id=int(sid) if sid else None,
        detail={"store_id": int(sid) if sid else None, "platform": data.platform, **result},
        request=request,
        store_id=int(sid) if sid else None,
    )
    return success(result, request=request)


@router.get("/category-maps", dependencies=[require_permission("product:read")])
async def list_category_maps(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
    platform: str | None = None,
):
    """平台分类映射列表（含本地分类名）。"""
    from app.services.store_scope import require_bound_store

    sid = require_bound_store(user, store_id)
    rows = await ProductSyncService.list_category_maps(db, sid, platform)
    cat_ids = list({r.local_category_id for r in rows})
    cat_names = {
        cid: name
        for cid, name in (
            await db.execute(
                select(ProductCategory.id, ProductCategory.name).where(ProductCategory.id.in_(cat_ids or [-1]))
            )
        ).all()
    }
    items = [
        {
            "id": r.id,
            "store_id": r.store_id,
            "platform": r.platform,
            "local_category_id": r.local_category_id,
            "local_category_name": cat_names.get(r.local_category_id),
            "platform_category_code": r.platform_category_code,
            "platform_category_name": r.platform_category_name,
            "status": r.status,
        }
        for r in rows
    ]
    return success({"items": items, "total": len(items)}, request=request)


@router.post("/category-maps", dependencies=[require_permission("product:update")])
async def create_category_map(data: CategoryMapPayload, request: Request, db: DbSession, user: CurrentUser):
    """新增/更新平台分类映射（store_id 留空=总部默认）。"""
    from app.services.store_scope import assert_store_access

    # 非超管：必须绑定门店，store_id 留空强制写本店，禁止创建总部级映射
    if user.role.code != "SUPER_ADMIN":
        if not user.store_id:
            raise AppException(40301, "账号未绑定门店", status_code=403)
        data.store_id = int(user.store_id)
    if data.store_id:
        assert_store_access(user, data.store_id)
    if not await db.get(ProductCategory, data.local_category_id):
        raise AppException(40454, "本地分类不存在", status_code=400)
    row = await ProductSyncService.upsert_category_map(
        db,
        platform=data.platform,
        local_category_id=data.local_category_id,
        platform_category_code=data.platform_category_code,
        platform_category_name=data.platform_category_name,
        store_id=data.store_id,
    )
    await db.commit()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.category_map.create",
        module="delivery",
        target_type="platform_category_map",
        target_id=int(row.id),
        detail={"store_id": int(data.store_id) if data.store_id else None, "platform": data.platform, "local_category_id": int(data.local_category_id)},
        request=request,
        store_id=int(data.store_id) if data.store_id else None,
    )
    return success({"id": row.id}, request=request)


@router.delete("/category-maps/{map_id}", dependencies=[require_permission("product:update")])
async def delete_category_map(map_id: int, request: Request, db: DbSession, user: CurrentUser):
    from app.services.store_scope import assert_store_access

    row = await db.get(PlatformCategoryMap, map_id)
    if not row:
        raise AppException(40451, "映射不存在", status_code=404)
    # store_id 为 NULL 的总部级映射仅超管可删除；assert_store_access 对非超管的 None 一律拒绝
    assert_store_access(user, row.store_id)
    ok = await ProductSyncService.delete_category_map(db, map_id)
    await db.commit()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.category_map.delete",
        module="delivery",
        target_type="platform_category_map",
        target_id=int(map_id),
        detail={"store_id": int(row.store_id) if row.store_id else None, "platform": row.platform, "deleted": bool(ok)},
        request=request,
        store_id=int(row.store_id) if row.store_id else None,
    )
    return success({"id": map_id, "deleted": ok}, request=request)


@router.get("/sync-alerts", dependencies=[require_permission("product:read")])
async def list_sync_alerts(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    platform: str | None = None,
    alert_type: str | None = None,
    resolved: int | None = None,
    page: int = 1,
    page_size: int = 50,
):
    """外卖同步/拒单告警列表。"""
    from sqlalchemy import func

    from app.core.response import calc_pages

    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 50)), 200)
    q = select(DeliverySyncAlert)
    if user.role.code != "SUPER_ADMIN":
        if not user.store_id:
            raise AppException(40301, "账号未绑定门店", status_code=403)
        q = q.where(DeliverySyncAlert.store_id == int(user.store_id))
    elif getattr(user, "store_id", None):
        q = q.where(DeliverySyncAlert.store_id == int(user.store_id))
    if platform:
        q = q.where(DeliverySyncAlert.platform == platform.upper())
    if alert_type:
        q = q.where(DeliverySyncAlert.alert_type == alert_type.strip().upper())
    if resolved is not None:
        q = q.where(DeliverySyncAlert.resolved == int(resolved))
    total = int(await db.scalar(select(func.count()).select_from(q.subquery())) or 0)
    rows = list(
        (
            await db.execute(
                q.order_by(DeliverySyncAlert.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": r.id,
            "store_id": r.store_id,
            "platform": r.platform,
            "alert_type": r.alert_type,
            "title": r.title,
            "detail": r.detail,
            "platform_order_id": r.platform_order_id,
            "resolved": r.resolved,
            "created_at": serialize_dt(r.created_at),
            "resolved_at": serialize_dt(r.resolved_at),
        }
        for r in rows
    ]
    return success(
        {"items": items, "total": total, "page": page, "page_size": page_size,
         "total_pages": calc_pages(total, page_size)},
        request=request,
    )


@router.post("/sync-alerts/{alert_id}/resolve", dependencies=[require_permission("product:update")])
async def resolve_sync_alert(alert_id: int, request: Request, db: DbSession, user: CurrentUser):
    from app.core.response import naive_local_now

    row = await db.get(DeliverySyncAlert, alert_id)
    if not row:
        raise AppException(40452, "告警不存在", status_code=404)
    # store_id 为 NULL 的平台级告警仅超管可处理；assert_store_access 对非超管的 None 一律拒绝
    from app.services.store_scope import assert_store_access

    assert_store_access(user, row.store_id)
    row.resolved = 1
    row.resolved_at = naive_local_now()
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="delivery.sync_alert.resolve",
        module="delivery",
        target_type="delivery_sync_alert",
        target_id=int(row.id),
        detail={"store_id": int(row.store_id) if row.store_id else None, "alert_type": row.alert_type, "title": row.title},
        request=request,
        store_id=int(row.store_id) if row.store_id else None,
    )
    return success({"id": row.id, "resolved": 1}, request=request)


@router.get("/sync/overview", dependencies=[require_permission("product:read")])
async def sync_overview(request: Request, db: DbSession, user: CurrentUser):
    """同步监控总览：各平台绑定状态 + 未解决告警数。"""
    from sqlalchemy import func

    if user.role.code != "SUPER_ADMIN" and not user.store_id:
        raise AppException(40301, "账号未绑定门店", status_code=403)
    q = select(PlatformStoreBinding)
    if getattr(user, "store_id", None):
        q = q.where(PlatformStoreBinding.store_id == int(user.store_id))
    bindings = list((await db.execute(q.order_by(PlatformStoreBinding.id.desc()))).scalars().all())

    unresolved_q = select(func.count()).select_from(DeliverySyncAlert).where(DeliverySyncAlert.resolved == 0)
    if getattr(user, "store_id", None):
        unresolved_q = unresolved_q.where(DeliverySyncAlert.store_id == int(user.store_id))
    unresolved = int(await db.scalar(unresolved_q) or 0)

    items = [
        {
            "binding_id": b.id,
            "store_id": b.store_id,
            "platform": b.platform,
            "platform_shop_id": b.platform_shop_id,
            "platform_shop_name": b.platform_shop_name,
            "status": b.status,
            "has_token": bool(b.access_token),
            "token_expires_at": serialize_dt(b.token_expires_at),
            "auto_sync_open": bool(b.auto_sync_open),
            "platform_open_status": b.platform_open_status,
            "last_sync_at": serialize_dt(b.last_sync_at),
        }
        for b in bindings
    ]
    return success({"bindings": items, "unresolved_alerts": unresolved}, request=request)

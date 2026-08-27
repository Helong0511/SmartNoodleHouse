"""美团 / 饿了么外卖接入 API"""
import asyncio

from pydantic import BaseModel, Field
from sqlalchemy import select

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.response import success
from app.models import DeliveryCallbackLog, DeliveryOrder, DeliveryProductMap, DeliveryRefund, PlatformStoreBinding, Product
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
    approve: bool
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
        return JSONResponse({"data": "ng", "error": {"msg": str(e)}}, status_code=200)
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
        return JSONResponse({"message": "error", "error": str(e)}, status_code=200)
    return JSONResponse({"message": "ok"})


@router.post("/orders/{order_id}/confirm", dependencies=[require_permission("order:update")])
async def confirm_delivery_order(
    order_id: int, request: Request, db: DbSession, user: CurrentUser, background_tasks: BackgroundTasks
):
    from app.models import Order
    from app.services.store_scope import assert_order_store_access

    existing = await db.get(Order, order_id)
    if not existing:
        raise HTTPException(status_code=404, detail="订单不存在")
    assert_order_store_access(user, existing)
    order, deferred = await DeliveryService.merchant_confirm(db, order_id)
    if deferred:
        background_tasks.add_task(run_deferred_tasks, deferred)
    await ws_manager.notify_order_event(
        "ORDER_CONFIRMED",
        {"order_id": order.id, "order_no": order.order_no, "status": order.status, "store_id": order.store_id},
    )
    return success({"id": order.id, "status": order.status}, request=request)


@router.get("/stats", dependencies=[require_permission("order:read")])
async def delivery_stats(request: Request, db: DbSession, user: CurrentUser):
    """外卖运营台顶部 KPI。"""
    from sqlalchemy import func

    from app.core.response import now_local
    from app.models import Order
    from app.services.store_scope import require_bound_store

    store = require_bound_store(user)
    today = now_local().replace(hour=0, minute=0, second=0, microsecond=0)

    async def _count(extra=None):
        q = select(func.count()).select_from(DeliveryOrder).join(Order, Order.id == DeliveryOrder.order_id)
        if store:
            q = q.where(Order.store_id == store)
        if extra is not None:
            q = q.where(extra)
        return int(await db.scalar(q) or 0)

    pending = await _count(DeliveryOrder.delivery_status == "PENDING")
    accepted = await _count(DeliveryOrder.delivery_status.in_(["ACCEPTED", "READY"]))
    delivering = await _count(
        DeliveryOrder.delivery_status.notin_(["PENDING", "ACCEPTED", "READY", "DONE", "CANCELLED"])
    )
    urging = await _count(
        (DeliveryOrder.urge_count > 0) & (DeliveryOrder.delivery_status.notin_(["DONE", "CANCELLED"]))
    )
    today_cnt = await _count(DeliveryOrder.created_at >= today)
    today_meituan = await _count((DeliveryOrder.created_at >= today) & (DeliveryOrder.platform == "MEITUAN"))
    today_eleme = await _count((DeliveryOrder.created_at >= today) & (DeliveryOrder.platform == "ELEME"))

    refund_pending_q = (
        select(func.count())
        .select_from(DeliveryRefund)
        .join(Order, Order.id == DeliveryRefund.order_id)
        .where(DeliveryRefund.status == "PENDING")
    )
    if store:
        refund_pending_q = refund_pending_q.where(Order.store_id == store)
    refund_pending = int(await db.scalar(refund_pending_q) or 0)

    from app.services.revenue_stats import net_revenue_since

    today_revenue = await net_revenue_since(
        db, since=today, store_id=store, channels=["MEITUAN", "ELEME"]
    )

    return success(
        {
            "pending": pending,
            "accepted": accepted,
            "delivering": delivering,
            "urging": urging,
            "refund_pending": refund_pending,
            "today_orders": today_cnt,
            "today_meituan": today_meituan,
            "today_eleme": today_eleme,
            "today_revenue": str(today_revenue),
        },
        request=request,
    )


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
    store = require_bound_store(user)
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
    if date_from:
        try:
            q = q.where(DeliveryOrder.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            # 含当天：传 YYYY-MM-DD 时加一天
            end = datetime.fromisoformat(date_to)
            if len(date_to) <= 10:
                from datetime import timedelta

                end = end + timedelta(days=1)
            q = q.where(DeliveryOrder.created_at < end)
        except ValueError:
            pass

    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    result = await db.execute(
        q.order_by(DeliveryOrder.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    rows = list(result.scalars().all())
    order_ids = [d.order_id for d in rows]
    order_map: dict[int, Order] = {}
    if order_ids:
        order_map = {
            o.id: o
            for o in (await db.execute(select(Order).where(Order.id.in_(order_ids)))).scalars().all()
        }

    items = []
    for d in rows:
        o = order_map.get(d.order_id)
        items.append(
            {
                "id": d.id,
                "order_id": d.order_id,
                "order_no": o.order_no if o else None,
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
                "expected_delivery_at": d.expected_delivery_at.isoformat() if d.expected_delivery_at else None,
                "completed_at": d.completed_at.isoformat() if d.completed_at else None,
                "created_at": d.created_at.isoformat() if d.created_at else None,
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
        raise HTTPException(status_code=404, detail="外卖订单不存在")
    order = await db.get(Order, d.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
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
            "expected_delivery_at": d.expected_delivery_at.isoformat() if d.expected_delivery_at else None,
            "completed_at": d.completed_at.isoformat() if d.completed_at else None,
            "created_at": d.created_at.isoformat() if d.created_at else None,
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
    page: int = 1,
    page_size: int = 20,
):
    from app.models import Order
    from app.services.store_scope import require_bound_store

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = select(DeliveryRefund).join(Order, Order.id == DeliveryRefund.order_id)
    store = require_bound_store(user)
    if store:
        q = q.where(Order.store_id == store)
    if status:
        q = q.where(DeliveryRefund.status == status.upper())
    from sqlalchemy import func

    from app.core.response import calc_pages

    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    result = await db.execute(
        q.order_by(DeliveryRefund.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
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
                "handled_at": r.handled_at.isoformat() if r.handled_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
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


@router.post("/refunds/{refund_id}/action", dependencies=[require_permission("order:update")])
async def refund_action(refund_id: int, data: RefundAction, request: Request, db: DbSession, user: CurrentUser):
    from app.models import Order
    from app.services.store_scope import assert_order_store_access

    refund = await db.get(DeliveryRefund, refund_id)
    if not refund:
        raise HTTPException(status_code=404, detail="退款单不存在")
    order = await db.get(Order, refund.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    assert_order_store_access(user, order)
    result = await DeliveryService.process_refund(db, refund_id, data.approve, data.reason)
    await ws_manager.notify_order_event(
        "ORDER_REFUND_HANDLED",
        {"refund_id": refund_id, "approve": data.approve, "store_id": order.store_id, **result},
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
        raise HTTPException(status_code=404, detail="外卖订单不存在")
    order = await db.get(Order, d_order.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    assert_order_store_access(user, order)
    result = await DeliveryService.cancel_by_merchant(db, delivery_order_id, data.reason)
    if result.get("order_id"):
        await ws_manager.notify_order_event(
            "ORDER_CANCELLED",
            {"order_id": result["order_id"], "reason": data.reason, "by": "merchant"},
        )
    return success(result, request=request)


@router.get("/callback-logs", dependencies=[require_permission("order:read")])
async def list_callback_logs(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    platform: str | None = None,
    event_type: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    from app.models import Order
    from app.services.store_scope import require_bound_store

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = select(DeliveryCallbackLog).outerjoin(Order, Order.id == DeliveryCallbackLog.order_id)
    store = require_bound_store(user)
    if store:
        q = q.where((DeliveryCallbackLog.order_id.is_(None)) | (Order.store_id == store))
    if platform:
        q = q.where(DeliveryCallbackLog.platform == platform.upper())
    if event_type:
        q = q.where(DeliveryCallbackLog.event_type == event_type)
    result = await db.execute(
        q.order_by(DeliveryCallbackLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    items = [
        {
            "id": log.id,
            "platform": log.platform,
            "event_type": log.event_type,
            "platform_order_id": log.platform_order_id,
            "order_id": log.order_id,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in result.scalars()
    ]
    return success({"items": items, "page": page, "page_size": page_size}, request=request)


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
            "platform": b.platform,
            "platform_shop_id": b.platform_shop_id,
            "platform_shop_name": b.platform_shop_name,
            "status": b.status,
            "has_token": bool(b.access_token),
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
        raise HTTPException(status_code=400, detail="缺少门店")
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
            existing.access_token = data.access_token
        if data.refresh_token:
            existing.refresh_token = data.refresh_token
        existing.status = 1
        await db.flush()
        return success({"id": existing.id, "updated": True}, request=request)

    row = PlatformStoreBinding(
        store_id=sid,
        platform=data.platform.upper(),
        platform_shop_id=data.platform_shop_id,
        platform_shop_name=data.platform_shop_name,
        access_token=data.access_token,
        refresh_token=data.refresh_token,
    )
    db.add(row)
    await db.flush()
    return success({"id": row.id, "updated": False}, request=request)


@router.post("/sync/menu", dependencies=[require_permission("product:update")])
async def sync_menu(
    request: Request, db: DbSession, user: CurrentUser, store_id: int | None = None, platform: str = "MEITUAN"
):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise HTTPException(status_code=400, detail="缺少门店")
    assert_store_access(user, sid)
    result = await ProductSyncService.sync_store_menu(db, int(sid), platform)
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
        raise HTTPException(status_code=400, detail="缺少门店")
    assert_store_access(user, sid)
    result = await ProductSyncService.sync_prices(db, int(sid), platform)
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
        raise HTTPException(status_code=400, detail="缺少门店")
    assert_store_access(user, sid)
    result = await ProductSyncService.sync_stock(db, int(sid), platform)
    return success(result, request=request)


@router.get("/product-maps", dependencies=[require_permission("product:read")])
async def list_product_maps(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    platform: str | None = None,
    store_id: int | None = None,
):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        return success([], request=request)
    assert_store_access(user, sid)
    store_id = int(sid)
    q = select(DeliveryProductMap).where(DeliveryProductMap.store_id == store_id)
    if platform:
        q = q.where(DeliveryProductMap.platform == platform.upper())
    result = await db.execute(q)
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
    return success(items, request=request)


@router.post("/product-maps", dependencies=[require_permission("product:update")])
async def create_product_map(data: ProductMapCreate, request: Request, db: DbSession, user: CurrentUser):
    from app.services.store_scope import assert_store_access, require_bound_store

    sid = require_bound_store(user, data.store_id)
    if sid is None:
        sid = int(user.store_id) if user.store_id else None
    if not sid:
        raise HTTPException(status_code=400, detail="缺少门店")
    assert_store_access(user, sid)
    product = await db.get(Product, data.product_id)
    if not product:
        return success(None, message="商品不存在", request=request)
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
    return success({"id": row.id, "updated": False}, request=request)

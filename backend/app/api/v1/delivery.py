"""美团 / 饿了么外卖接入 API"""
import asyncio

from pydantic import BaseModel, Field
from sqlalchemy import select

from fastapi import APIRouter, BackgroundTasks, Request
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
    store_id: int = 1
    platform: str = Field(description="MEITUAN 或 ELEME")
    platform_sku_id: str
    platform_spu_code: str | None = None
    app_food_code: str | None = None
    option_signature: str | None = None
    platform_sku_name: str | None = None
    product_id: int
    option_map_json: str | None = None


class StoreBindingCreate(BaseModel):
    store_id: int = 1
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
    """美团推送回调：须在 2s 内返回 {"data":"ok"}"""
    body = await request.body()
    headers = dict(request.headers)
    deferred: list = []
    try:
        result = await DeliveryService.handle_notify(db, "MEITUAN", headers, body)
        deferred = result.deferred_tasks
        background_tasks.add_task(_after_notify, result, "MEITUAN", deferred)
    except Exception as e:
        return JSONResponse({"data": "ng", "error": {"msg": str(e)}}, status_code=200)
    return JSONResponse({"data": "ok"})


@router.post("/eleme/notify")
async def eleme_notify(request: Request, db: DbSession, background_tasks: BackgroundTasks):
    """饿了么推送回调：成功返回 {"message":"ok"}"""
    body = await request.body()
    headers = dict(request.headers)
    try:
        result = await DeliveryService.handle_notify(db, "ELEME", headers, body)
        background_tasks.add_task(_after_notify, result, "ELEME", result.deferred_tasks)
    except Exception as e:
        return JSONResponse({"message": "error", "error": str(e)}, status_code=200)
    return JSONResponse({"message": "ok"})


@router.get("/orders", dependencies=[require_permission("order:read")])
async def list_delivery_orders(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    platform: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    q = select(DeliveryOrder)
    if platform:
        q = q.where(DeliveryOrder.platform == platform.upper())
    result = await db.execute(
        q.order_by(DeliveryOrder.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    items = []
    for d in result.scalars():
        items.append(
            {
                "id": d.id,
                "order_id": d.order_id,
                "platform": d.platform,
                "platform_order_id": d.platform_order_id,
                "platform_day_seq": d.platform_day_seq,
                "delivery_status": d.delivery_status,
                "logistics_status": d.logistics_status,
                "logistics_code": d.logistics_code,
                "refund_status": d.refund_status,
                "urge_count": d.urge_count,
                "recipient_name": d.recipient_name,
                "recipient_phone": d.recipient_phone,
                "recipient_address": d.recipient_address,
                "rider_name": d.rider_name,
                "rider_phone": d.rider_phone,
                "delivery_fee": str(d.delivery_fee),
                "completed_at": d.completed_at.isoformat() if d.completed_at else None,
            }
        )
    return success({"items": items, "page": page, "page_size": page_size}, request=request)


@router.get("/refunds", dependencies=[require_permission("order:read")])
async def list_refunds(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    q = select(DeliveryRefund)
    if status:
        q = q.where(DeliveryRefund.status == status.upper())
    result = await db.execute(
        q.order_by(DeliveryRefund.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    items = []
    for r in result.scalars():
        items.append(
            {
                "id": r.id,
                "order_id": r.order_id,
                "platform": r.platform,
                "platform_refund_id": r.platform_refund_id,
                "refund_type": r.refund_type,
                "amount": str(r.amount),
                "reason": r.reason,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return success({"items": items, "page": page, "page_size": page_size}, request=request)


@router.post("/refunds/{refund_id}/action", dependencies=[require_permission("order:update")])
async def refund_action(refund_id: int, data: RefundAction, request: Request, db: DbSession, user: CurrentUser):
    result = await DeliveryService.process_refund(db, refund_id, data.approve, data.reason)
    await ws_manager.notify_order_event(
        "ORDER_REFUND_HANDLED",
        {"refund_id": refund_id, "approve": data.approve, **result},
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
    q = select(DeliveryCallbackLog)
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
async def list_store_bindings(request: Request, db: DbSession, user: CurrentUser, store_id: int = 1):
    result = await db.execute(
        select(PlatformStoreBinding).where(PlatformStoreBinding.store_id == store_id).order_by(PlatformStoreBinding.platform)
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
    return success(items, request=request)


@router.post("/store-bindings", dependencies=[require_permission("product:update")])
async def upsert_store_binding(data: StoreBindingCreate, request: Request, db: DbSession, user: CurrentUser):
    existing = (
        await db.execute(
            select(PlatformStoreBinding).where(
                PlatformStoreBinding.store_id == data.store_id,
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
        store_id=data.store_id,
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
async def sync_menu(request: Request, db: DbSession, user: CurrentUser, store_id: int = 1, platform: str = "MEITUAN"):
    result = await ProductSyncService.sync_store_menu(db, store_id, platform)
    return success(result, request=request)


@router.post("/sync/prices", dependencies=[require_permission("product:update")])
async def sync_prices(request: Request, db: DbSession, user: CurrentUser, store_id: int = 1, platform: str = "MEITUAN"):
    result = await ProductSyncService.sync_prices(db, store_id, platform)
    return success(result, request=request)


@router.post("/sync/stock", dependencies=[require_permission("product:update")])
async def sync_stock(request: Request, db: DbSession, user: CurrentUser, store_id: int = 1, platform: str = "MEITUAN"):
    result = await ProductSyncService.sync_stock(db, store_id, platform)
    return success(result, request=request)


@router.get("/product-maps", dependencies=[require_permission("product:read")])
async def list_product_maps(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    platform: str | None = None,
    store_id: int = 1,
):
    q = select(DeliveryProductMap).where(DeliveryProductMap.store_id == store_id)
    if platform:
        q = q.where(DeliveryProductMap.platform == platform.upper())
    result = await db.execute(q)
    items = []
    for m in result.scalars():
        product = await db.get(Product, m.product_id)
        items.append(
            {
                "id": m.id,
                "platform": m.platform,
                "platform_sku_id": m.platform_sku_id,
                "platform_spu_code": m.platform_spu_code,
                "app_food_code": m.app_food_code,
                "option_signature": m.option_signature,
                "platform_sku_name": m.platform_sku_name,
                "product_id": m.product_id,
                "product_name": product.name if product else None,
                "status": m.status,
            }
        )
    return success(items, request=request)


@router.post("/product-maps", dependencies=[require_permission("product:update")])
async def create_product_map(data: ProductMapCreate, request: Request, db: DbSession, user: CurrentUser):
    product = await db.get(Product, data.product_id)
    if not product:
        return success(None, message="商品不存在", request=request)
    existing = await db.execute(
        select(DeliveryProductMap).where(
            DeliveryProductMap.store_id == data.store_id,
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
        store_id=data.store_id,
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

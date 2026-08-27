from fastapi import APIRouter, HTTPException, Request

from sqlalchemy import func, select

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, StaffOrCustomer, require_kitchen, require_permission

from app.core.response import calc_pages, success

from app.models import DeliveryOrder, DiningTable, Order, OrderItem, OrderItemOption, Store

from app.schemas.order import OrderCreate, OrderStatusUpdate

from app.services.customer_service import OrderCreateService

from app.services.delivery_service import DeliveryService

from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/orders", tags=["订单"])


def _table_payload(table: DiningTable | None) -> dict | None:
    if not table:
        return None
    return {
        "id": table.id,
        "table_no": table.table_no,
        "table_name": table.table_name,
    }


async def _load_tables(db, table_ids: list[int]) -> dict[int, DiningTable]:
    ids = [i for i in table_ids if i]
    if not ids:
        return {}
    rows = await db.execute(select(DiningTable).where(DiningTable.id.in_(ids)))
    return {t.id: t for t in rows.scalars().all()}


async def _load_stores(db, store_ids: list[int]) -> dict[int, Store]:
    ids = list({i for i in store_ids if i})
    if not ids:
        return {}
    rows = await db.execute(select(Store).where(Store.id.in_(ids)))
    return {s.id: s for s in rows.scalars().all()}


@router.post("")
async def create_order(
    data: OrderCreate,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    order = await OrderCreateService.create_order(
        db,
        store_id=data.store_id,
        table_token=data.table_token,
        customer_id=customer.id,
        items=[i.model_dump() for i in data.items],
        remark=data.remark,
        channel=data.channel,
        idempotency_key=data.idempotency_key,
        need_pack=data.need_pack,
        dining_type=data.dining_type,
    )
    table = await db.get(DiningTable, order.table_id) if order.table_id else None
    await ws_manager.notify_order_event(
        "ORDER_CREATED",
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "status": order.status,
            "store_id": order.store_id,
            "table_id": order.table_id,
            "table_no": table.table_no if table else None,
            "dining_type": order.dining_type,
        },
    )
    return success(
        {
            "id": order.id,
            "order_no": order.order_no,
            "total_amount": str(order.total_amount),
            "subtotal": str(order.subtotal),
            "packing_fee": str(order.packing_fee),
            "dining_type": order.dining_type,
            "status": order.status,
            "payment_status": order.payment_status,
            "table_id": order.table_id,
            "table": _table_payload(table),
        },
        request=request,
    )

@router.get("")

async def list_orders(

    request: Request,

    db: DbSession,

    user: CurrentUser,

    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    payment_status: str | None = None,
    store_id: int | None = None,
):

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = select(Order)
    from app.services.store_scope import require_bound_store

    effective_store = require_bound_store(user, store_id)
    if effective_store:
        q = q.where(Order.store_id == effective_store)

    if status:

        q = q.where(Order.status == status)

    if payment_status:

        q = q.where(Order.payment_status == payment_status)

    total = await db.scalar(select(func.count()).select_from(q.subquery()))

    result = await db.execute(q.order_by(Order.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    order_rows = list(result.scalars().all())
    table_map = await _load_tables(db, [o.table_id for o in order_rows if o.table_id])
    store_map = await _load_stores(db, [o.store_id for o in order_rows if o.store_id])

    items = []
    for o in order_rows:
        t = table_map.get(o.table_id) if o.table_id else None
        store = store_map.get(o.store_id)
        items.append(
            {
                "id": o.id,
                "order_no": o.order_no,
                "store_id": o.store_id,
                "store_name": store.name if store else None,
                "customer_id": o.customer_id,
                "table_id": o.table_id,
                "table_no": t.table_no if t else None,
                "table_name": t.table_name if t else None,
                "dining_type": o.dining_type,
                "packing_fee": str(o.packing_fee),
                "status": o.status,
                "channel": o.channel,
                "total_amount": str(o.total_amount),
                "payment_status": o.payment_status,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
        )

    return success({"items": items, "total": total or 0, "page": page, "page_size": page_size, "total_pages": calc_pages(total or 0, page_size)}, request=request)


@router.get("/kitchen/stores", dependencies=[require_kitchen()])
async def kitchen_stores(request: Request, db: DbSession, user: CurrentUser):
    """KDS 门店切换列表：超管看全部营业门店；门店账号仅本店。"""
    from app.models import Store

    if user.role.code == "SUPER_ADMIN":
        rows = list((await db.execute(select(Store).where(Store.status == 1).order_by(Store.id))).scalars().all())
    else:
        if not user.store_id:
            raise HTTPException(status_code=403, detail="账号未绑定门店")
        store = await db.get(Store, user.store_id)
        rows = [store] if store else []
    return success(
        {
            "items": [
                {"id": s.id, "store_no": s.store_no, "name": s.name, "status": s.status}
                for s in rows
                if s
            ],
            "is_super_admin": user.role.code == "SUPER_ADMIN",
            "bound_store_id": user.store_id,
        },
        request=request,
    )


@router.get("/kitchen/active", dependencies=[require_kitchen()])
async def kitchen_orders(request: Request, db: DbSession, user: CurrentUser, store_id: int | None = None):
    """厨房 KDS：批量加载明细/规格/外卖信息，消除 N+1；按门店隔离。"""
    from app.core.response import now_local
    from app.models import Store
    from app.services.store_scope import require_bound_store

    effective_store = require_bound_store(user, store_id)
    q = select(Order).where(Order.status.in_(["PAID", "CONFIRMED", "PREPARING", "COOKING", "READY"]))
    if effective_store:
        q = q.where(Order.store_id == effective_store)
    result = await db.execute(q.order_by(Order.paid_at.is_(None), Order.paid_at, Order.id))
    order_list = list(result.scalars().all())
    if not order_list:
        return success([], request=request)

    order_ids = [o.id for o in order_list]
    items = list(
        (await db.execute(select(OrderItem).where(OrderItem.order_id.in_(order_ids)))).scalars().all()
    )
    item_ids = [i.id for i in items]
    opts = []
    if item_ids:
        opts = list(
            (
                await db.execute(select(OrderItemOption).where(OrderItemOption.order_item_id.in_(item_ids)))
            ).scalars().all()
        )
    deliveries = list(
        (await db.execute(select(DeliveryOrder).where(DeliveryOrder.order_id.in_(order_ids)))).scalars().all()
    )

    opts_by_item: dict[int, list] = {}
    for o in opts:
        opts_by_item.setdefault(o.order_item_id, []).append(o)
    items_by_order: dict[int, list] = {}
    for it in items:
        items_by_order.setdefault(it.order_id, []).append(it)
    delivery_by_order = {d.order_id: d for d in deliveries}
    table_map = await _load_tables(db, [o.table_id for o in order_list if o.table_id])
    store_ids = list({o.store_id for o in order_list if o.store_id})
    store_map = {}
    if store_ids:
        store_map = {
            s.id: s
            for s in (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars().all()
        }

    channel_label_map = {
        "WECHAT": "微信堂食",
        "ALIPAY": "支付宝堂食",
        "MEITUAN": "美团外卖",
        "ELEME": "饿了么",
        "H5": "H5点餐",
    }
    now = now_local()
    orders = []
    for order in order_list:
        order_items = []
        for item in items_by_order.get(order.id, []):
            order_items.append(
                {
                    "id": item.id,
                    "product_name": item.product_name_snapshot,
                    "quantity": item.quantity,
                    "options": [o.option_name_snapshot for o in opts_by_item.get(item.id, [])],
                    "remark": item.remark,
                }
            )
        delivery_info = None
        d = delivery_by_order.get(order.id)
        if d:
            delivery_info = {
                "platform": d.platform,
                "day_seq": d.platform_day_seq,
                "address": d.recipient_address,
                "phone": d.recipient_phone,
                "delivery_status": d.delivery_status,
                "logistics_status": d.logistics_status,
                "urge_count": d.urge_count or 0,
                "refund_status": d.refund_status,
                "rider_name": d.rider_name,
                "rider_phone": d.rider_phone,
            }
        t = table_map.get(order.table_id) if order.table_id else None
        anchor = order.paid_at or order.ordered_at or order.created_at
        wait_seconds = 0
        if anchor is not None:
            try:
                a = anchor.replace(tzinfo=None) if getattr(anchor, "tzinfo", None) else anchor
                n = now.replace(tzinfo=None) if getattr(now, "tzinfo", None) else now
                wait_seconds = max(0, int((n - a).total_seconds()))
            except Exception:
                wait_seconds = 0
        packing = float(order.packing_fee or 0)
        store = store_map.get(order.store_id)
        orders.append(
            {
                "id": order.id,
                "order_no": order.order_no,
                "table_id": order.table_id,
                "table_no": t.table_no if t else None,
                "table_name": t.table_name if t else None,
                "dining_type": order.dining_type,
                "packing_fee": str(order.packing_fee or "0.00"),
                "need_pack": (order.dining_type or "") == "TAKEAWAY" or packing > 0,
                "remark": order.remark,
                "store_id": order.store_id,
                "store_name": store.name if store else None,
                "store_no": store.store_no if store else None,
                "status": order.status,
                "channel": order.channel,
                "channel_label": channel_label_map.get(order.channel or "", order.channel or "堂食"),
                "is_delivery": order.channel in ("MEITUAN", "ELEME"),
                "paid_at": order.paid_at.isoformat() if order.paid_at else None,
                "ordered_at": order.ordered_at.isoformat() if order.ordered_at else None,
                "wait_seconds": wait_seconds,
                "delivery": delivery_info,
                "items": order_items,
            }
        )
    return success(orders, request=request)


@router.get("/mine")
async def list_my_orders(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    page: int = 1,
    page_size: int = 20,
):
    """顾客历史订单列表（会员中心）"""
    from app.services.order_service import ORDER_STATUS_LABEL, PAYMENT_STATUS_LABEL

    page = max(1, page)
    page_size = min(max(1, page_size), 50)
    base = select(Order).where(Order.customer_id == customer.id)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = list(
        (
            await db.execute(
                base.order_by(Order.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars()
    )
    if not rows:
        return success(
            {"items": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0},
            request=request,
        )

    order_ids = [o.id for o in rows]
    items = list((await db.execute(select(OrderItem).where(OrderItem.order_id.in_(order_ids)))).scalars())
    items_by_order: dict[int, list[OrderItem]] = {}
    for it in items:
        items_by_order.setdefault(it.order_id, []).append(it)
    table_map = await _load_tables(db, [o.table_id for o in rows if o.table_id])

    payload = []
    for o in rows:
        o_items = items_by_order.get(o.id, [])
        names = [i.product_name_snapshot for i in o_items[:3]]
        if len(o_items) > 3:
            names.append(f"等{len(o_items)}件")
        t = table_map.get(o.table_id) if o.table_id else None
        payload.append(
            {
                "id": o.id,
                "order_no": o.order_no,
                "status": o.status,
                "status_label": ORDER_STATUS_LABEL.get(o.status or "", o.status or ""),
                "payment_status": o.payment_status,
                "payment_status_label": PAYMENT_STATUS_LABEL.get(o.payment_status or "", o.payment_status or ""),
                "channel": o.channel,
                "dining_type": o.dining_type,
                "total_amount": str(o.total_amount),
                "item_count": len(o_items),
                "item_summary": "、".join(names) if names else "（无明细）",
                "table": _table_payload(t),
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "can_reorder": o.status not in ("PENDING_PAYMENT",) and len(o_items) > 0,
            }
        )

    return success(
        {
            "items": payload,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
        },
        request=request,
    )


@router.get("/{order_id}")

async def get_order(order_id: int, request: Request, db: DbSession, auth: StaffOrCustomer):

    user, customer = auth

    order = await db.get(Order, order_id)

    if not order:

        return success(None, message="订单不存在", request=request)

    if user is None and customer is None:

        raise HTTPException(status_code=401, detail="未登录")

    if user is not None:
        from app.services.store_scope import assert_order_store_access
        assert_order_store_access(user, order)

    if customer and order.customer_id != customer.id:

        raise HTTPException(status_code=403, detail="无权查看此订单")

    from app.services.order_service import ORDER_STATUS_LABEL, PAYMENT_STATUS_LABEL

    items_result = await db.execute(select(OrderItem).where(OrderItem.order_id == order_id))

    items = []

    for item in items_result.scalars():

        opts_result = await db.execute(select(OrderItemOption).where(OrderItemOption.order_item_id == item.id))

        items.append({

            "id": item.id,

            "product_id": item.product_id,

            "product_name": item.product_name_snapshot,

            "unit_price": str(item.unit_price_snapshot),

            "quantity": item.quantity,

            "subtotal": str(item.subtotal),

            "options": [

                {"group": o.group_name_snapshot, "name": o.option_name_snapshot, "code": o.option_code, "extra_price": str(o.extra_price_snapshot)}

                for o in opts_result.scalars()

            ],

        })

    store = await db.get(Store, order.store_id) if order.store_id else None

    return success(

        {

            "id": order.id,

            "order_no": order.order_no,

            "store_id": order.store_id,

            "store_name": store.name if store else None,

            "status": order.status,

            "status_label": ORDER_STATUS_LABEL.get(order.status or "", order.status or ""),

            "payment_status": order.payment_status,

            "payment_status_label": PAYMENT_STATUS_LABEL.get(order.payment_status or "", order.payment_status or ""),

            "channel": order.channel,

            "subtotal": str(order.subtotal),

            "total_amount": str(order.total_amount),

            "table_id": order.table_id,

            "table": _table_payload(await db.get(DiningTable, order.table_id) if order.table_id else None),

            "dining_type": order.dining_type,

            "packing_fee": str(order.packing_fee),

            "remark": order.remark,

            "created_at": order.created_at.isoformat() if order.created_at else None,

            "items": items,

            "can_reorder": bool(items) and order.status not in ("PENDING_PAYMENT",),

        },

        request=request,

    )


@router.post("/{order_id}/reorder")
async def reorder_order(order_id: int, request: Request, db: DbSession, customer: CurrentCustomer):
    """根据历史订单生成可再次下单的购物车条目（不直接创建新订单，便于用户确认）"""
    from app.services.order_service import OrderReorderService

    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.customer_id != customer.id:
        raise HTTPException(status_code=403, detail="无权复购此订单")
    if order.status == "PENDING_PAYMENT":
        from app.core.exceptions import AppException

        raise AppException(40042, "待支付订单请先完成支付或取消后再复购")

    data = await OrderReorderService.build_cart_from_order(db, order)
    return success(data, request=request)


@router.patch("/{order_id}/status", dependencies=[require_kitchen()])

async def update_order_status(order_id: int, data: OrderStatusUpdate, request: Request, db: DbSession, user: CurrentUser):
    from app.services.store_scope import assert_order_store_access
    existing = await db.get(Order, order_id)
    if not existing:
        raise HTTPException(status_code=404, detail="订单不存在")
    assert_order_store_access(user, existing)
    order = await OrderCreateService.update_status(db, order_id, data.status, operator_id=user.id)

    await DeliveryService.sync_kitchen_status(db, order)

    await ws_manager.notify_order_event(

        f"ORDER_{data.status}",

        {
            "order_id": order.id,
            "order_no": order.order_no,
            "status": order.status,
            "channel": order.channel,
            "store_id": order.store_id,
            "table_id": order.table_id,
        },

    )

    return success({"id": order.id, "status": order.status}, request=request)


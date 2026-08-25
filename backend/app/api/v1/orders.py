from fastapi import APIRouter, HTTPException, Request

from sqlalchemy import func, select



from app.api.deps import CurrentCustomer, CurrentUser, DbSession, StaffOrCustomer, require_kitchen, require_permission

from app.core.response import calc_pages, success

from app.models import DeliveryOrder, Order, OrderItem, OrderItemOption

from app.schemas.order import OrderCreate, OrderStatusUpdate

from app.services.customer_service import OrderCreateService

from app.services.delivery_service import DeliveryService

from app.services.websocket_manager import ws_manager



router = APIRouter(prefix="/orders", tags=["订单"])





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

    )

    await ws_manager.notify_order_event("ORDER_CREATED", {"order_id": order.id, "order_no": order.order_no, "status": order.status})

    return success(

        {

            "id": order.id,

            "order_no": order.order_no,

            "total_amount": str(order.total_amount),

            "status": order.status,

            "payment_status": order.payment_status,

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

):

    q = select(Order)

    if status:

        q = q.where(Order.status == status)

    if payment_status:

        q = q.where(Order.payment_status == payment_status)

    total = await db.scalar(select(func.count()).select_from(q.subquery()))

    result = await db.execute(q.order_by(Order.created_at.desc()).offset((page - 1) * page_size).limit(page_size))

    items = [

        {

            "id": o.id,

            "order_no": o.order_no,

            "customer_id": o.customer_id,

            "table_id": o.table_id,

            "status": o.status,

            "channel": o.channel,

            "total_amount": str(o.total_amount),

            "payment_status": o.payment_status,

            "created_at": o.created_at.isoformat() if o.created_at else None,

        }

        for o in result.scalars()

    ]

    return success({"items": items, "total": total or 0, "page": page, "page_size": page_size, "total_pages": calc_pages(total or 0, page_size)}, request=request)





@router.get("/kitchen/active", dependencies=[require_kitchen()])

async def kitchen_orders(request: Request, db: DbSession):

    result = await db.execute(

        select(Order).where(Order.status.in_(["PAID", "CONFIRMED", "PREPARING", "READY"])).order_by(Order.paid_at)

    )

    orders = []

    for order in result.scalars():

        items_result = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))

        items = []

        for item in items_result.scalars():

            opts_result = await db.execute(select(OrderItemOption).where(OrderItemOption.order_item_id == item.id))

            items.append({

                "product_name": item.product_name_snapshot,

                "quantity": item.quantity,

                "options": [o.option_name_snapshot for o in opts_result.scalars()],

            })

        delivery_info = None

        if order.channel in ("MEITUAN", "ELEME"):

            d = (

                await db.execute(select(DeliveryOrder).where(DeliveryOrder.order_id == order.id))

            ).scalar_one_or_none()

            if d:

                delivery_info = {

                    "platform": d.platform,

                    "day_seq": d.platform_day_seq,

                    "address": d.recipient_address,

                    "phone": d.recipient_phone,

                    "delivery_status": d.delivery_status,

                    "logistics_status": d.logistics_status,

                    "urge_count": d.urge_count,

                    "refund_status": d.refund_status,

                    "rider_name": d.rider_name,

                    "rider_phone": d.rider_phone,

                }

        channel_label = {

            "WECHAT": "微信堂食",

            "ALIPAY": "支付宝堂食",

            "MEITUAN": "美团外卖",

            "ELEME": "饿了么",

        }.get(order.channel or "", order.channel or "堂食")

        orders.append({

            "id": order.id,

            "order_no": order.order_no,

            "table_id": order.table_id,

            "status": order.status,

            "channel": order.channel,

            "channel_label": channel_label,

            "is_delivery": order.channel in ("MEITUAN", "ELEME"),

            "delivery": delivery_info,

            "items": items,

        })

    return success(orders, request=request)





@router.get("/{order_id}")

async def get_order(order_id: int, request: Request, db: DbSession, auth: StaffOrCustomer):

    user, customer = auth

    order = await db.get(Order, order_id)

    if not order:

        return success(None, message="订单不存在", request=request)

    if customer and order.customer_id != customer.id:

        raise HTTPException(status_code=403, detail="无权查看此订单")

    if user is None and customer is None:

        raise HTTPException(status_code=401, detail="未登录")



    items_result = await db.execute(select(OrderItem).where(OrderItem.order_id == order_id))

    items = []

    for item in items_result.scalars():

        opts_result = await db.execute(select(OrderItemOption).where(OrderItemOption.order_item_id == item.id))

        items.append({

            "id": item.id,

            "product_name": item.product_name_snapshot,

            "unit_price": str(item.unit_price_snapshot),

            "quantity": item.quantity,

            "subtotal": str(item.subtotal),

            "options": [

                {"group": o.group_name_snapshot, "name": o.option_name_snapshot, "code": o.option_code, "extra_price": str(o.extra_price_snapshot)}

                for o in opts_result.scalars()

            ],

        })

    return success(

        {

            "id": order.id,

            "order_no": order.order_no,

            "status": order.status,

            "payment_status": order.payment_status,

            "channel": order.channel,

            "subtotal": str(order.subtotal),

            "total_amount": str(order.total_amount),

            "items": items,

        },

        request=request,

    )





@router.patch("/{order_id}/status", dependencies=[require_permission("order:update")])

async def update_order_status(order_id: int, data: OrderStatusUpdate, request: Request, db: DbSession):

    order = await OrderCreateService.update_status(db, order_id, data.status)

    await DeliveryService.sync_kitchen_status(db, order)

    await ws_manager.notify_order_event(

        f"ORDER_{data.status}",

        {"order_id": order.id, "order_no": order.order_no, "status": order.status, "channel": order.channel},

    )

    return success({"id": order.id, "status": order.status}, request=request)


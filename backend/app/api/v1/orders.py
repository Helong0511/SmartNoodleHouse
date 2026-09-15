import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, or_, select

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, StaffOrCustomer, require_kitchen, require_permission, require_pos
from app.core.exceptions import AppException
from app.core.redis_client import get_redis
from app.core.response import calc_pages, ensure_local_aware, money, now_local, success
from app.models import DeliveryOrder, DiningTable, Order, OrderItem, OrderItemOption, Product, Store
from app.schemas.order import OrderCreate, OrderStatusUpdate, PosAddItems, PosOrderCreate
from app.services.customer_service import OrderCreateService
from app.services.delivery_service import DeliveryService
from app.services.store_scope import assert_order_store_access, assert_store_access, require_bound_store
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/orders", tags=["订单"])


def _apply_date_range(q, col, date_from: str | None, date_to: str | None):
    if date_from:
        try:
            q = q.where(col >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            end = datetime.fromisoformat(date_to)
            if len(date_to) <= 10:
                end = end + timedelta(days=1)
            q = q.where(col < end)
        except ValueError:
            pass
    return q


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


def _resolve_customer_order_channel(request: Request, client_channel: str | None) -> str:
    """顾客下单渠道由服务端根据 JWT channel 强制确定，客户端不可伪造 POS/外卖等。"""
    from app.core.exceptions import AppException
    from app.core.security import decode_customer_token

    allowed = {"WECHAT", "ALIPAY", "H5"}
    jwt_ch = ""
    auth = request.headers.get("authorization") or ""
    if auth.startswith("Bearer "):
        try:
            jwt_ch = str(decode_customer_token(auth.split(" ", 1)[1]).get("channel") or "").strip().upper()
        except Exception:
            jwt_ch = ""
    if jwt_ch in ("WECHAT", "ALIPAY"):
        return jwt_ch
    hint = (client_channel or "").strip().upper()
    if hint in allowed:
        # H5 入口无平台 JWT channel 时允许 WECHAT/ALIPAY/H5，仍拒绝 POS/外卖
        return hint if hint != "H5" else "H5"
    if hint:
        raise AppException(40009, f"非法下单渠道: {hint}")
    # 无 JWT channel 且未传：默认 H5（扫码页）
    return "H5"


@router.post("")
async def create_order(
    data: OrderCreate,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    if not (data.idempotency_key or "").strip():
        raise AppException(40010, "创建订单必须提供 idempotency_key，以便网络重试安全复用")
    channel = _resolve_customer_order_channel(request, data.channel)
    order = await OrderCreateService.create_order(
        db,
        store_id=data.store_id,
        table_token=data.table_token,
        customer_id=customer.id,
        items=[i.model_dump() for i in data.items],
        remark=data.remark,
        channel=channel,
        idempotency_key=data.idempotency_key,
        need_pack=data.need_pack,
        dining_type=data.dining_type,
        customer_coupon_id=data.customer_coupon_id,
        customer_voucher_id=data.customer_voucher_id,
        operator_id=None,
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
            "discount_amount": str(order.discount_amount),
            "coupon_discount": str(order.coupon_discount or "0.00"),
            "voucher_discount": str(order.voucher_discount or "0.00"),
            "packing_fee": str(order.packing_fee),
            "dining_type": order.dining_type,
            "status": order.status,
            "payment_status": order.payment_status,
            "table_id": order.table_id,
            "table": _table_payload(table),
        },
        request=request,
    )


@router.post("/pos", dependencies=[require_pos()])
async def create_pos_order(
    data: PosOrderCreate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """门店收银台下单（现金/扫码收款前创建待支付订单）。"""
    dtype = (data.dining_type or "").strip().upper()
    if dtype not in ("DINE_IN", "TAKEAWAY"):
        dtype = "TAKEAWAY" if data.need_pack else "DINE_IN"
    need_pack = data.need_pack or dtype == "TAKEAWAY"

    store_id = require_bound_store(user, data.store_id)
    table_token: str | None = None
    if dtype == "DINE_IN" and data.table_id:
        # 开台模式：选了桌号才校验
        table = await db.get(DiningTable, int(data.table_id))
        if not table or table.status != 1:
            raise AppException(40403, "桌台不存在或已停用")
        assert_store_access(user, table.store_id)
        store_id = table.store_id
        table_token = (table.qr_token or "").strip() or None
        if not table_token:
            raise AppException(40008, "桌台未配置二维码，请在后台重新生成桌码")
        # 同桌号挂单查重：一个桌号只能有一个未结算订单
        dup = await db.execute(
            select(Order.id, Order.order_no).where(
                Order.table_id == int(data.table_id),
                Order.status == "PENDING_PAYMENT",
                Order.channel == "POS",
            )
        )
        dup_row = dup.first()
        if dup_row:
            raise AppException(40060, f"桌台 {table.table_no} 已有挂单（单号 {dup_row[1]}），请先结算或加菜")
    elif store_id is None:
        raise AppException(40008, "请选择门店")

    from app.services.pos_shift_service import PosShiftService

    await PosShiftService.require_open_shift(db, user.id, store_id=int(store_id))

    order = await OrderCreateService.create_order(
        db,
        store_id=store_id,
        table_token=table_token,
        customer_id=None,
        items=[i.model_dump() for i in data.items],
        remark=data.remark,
        channel="POS",
        idempotency_key=data.idempotency_key,
        need_pack=need_pack,
        dining_type=dtype,
        waive_packing_fee=bool(data.waive_packing_fee),
        operator_id=user.id,
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
            "channel": "POS",
        },
    )
    return success(
        {
            "id": order.id,
            "order_no": order.order_no,
            "total_amount": str(order.total_amount),
            "subtotal": str(order.subtotal),
            "discount_amount": str(order.discount_amount),
            "packing_fee": str(order.packing_fee),
            "dining_type": order.dining_type,
            "status": order.status,
            "payment_status": order.payment_status,
            "table_id": order.table_id,
            "table": _table_payload(table),
        },
        request=request,
    )


@router.post("/{order_id}/add-items", dependencies=[require_pos()])
async def add_items_to_order(
    order_id: int,
    data: PosAddItems,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """收银台加菜：往已有待支付订单追加商品，重算金额。"""
    order = await db.get(Order, order_id)
    if not order:
        raise AppException(40404, "订单不存在")
    assert_order_store_access(user, order)
    if order.channel != "POS":
        raise AppException(40009, "仅收银台订单可加菜")
    if order.status != "PENDING_PAYMENT":
        raise AppException(40009, f"订单当前状态不可加菜: {order.status}")

    # 加菜幂等防重（Redis SET NX + 5min TTL）
    raw_key = (data.idempotency_key or "").strip()
    redis = await get_redis()
    idem_cache_key = f"pos:add:{int(order.id)}:{int(user.id)}:{raw_key}"[:200]
    if raw_key and redis:
        if redis.set(idem_cache_key, "1", nx=True, ex=300):
            pass  # 抢到锁，继续加菜
        else:
            # 幂等命中：返回当前订单快照
            await db.refresh(order)
            table = await db.get(DiningTable, order.table_id) if order.table_id else None
            return success(
                {
                    "id": order.id,
                    "order_no": order.order_no,
                    "total_amount": str(order.total_amount),
                    "subtotal": str(order.subtotal),
                    "discount_amount": str(order.discount_amount),
                    "packing_fee": str(order.packing_fee),
                    "status": order.status,
                    "payment_status": order.payment_status,
                    "table": _table_payload(table),
                },
                request=request,
            )

    items_data = [i.model_dump() for i in data.items]
    from app.services.order_service import OrderPriceCalculator
    add_subtotal, add_packing, add_discountable = await OrderPriceCalculator.build_order_items(
        db, order, items_data, deduct_stock=True, need_pack=(order.dining_type == "TAKEAWAY"),
    )
    # 重算金额：原 subtotal + 加菜 subtotal
    new_subtotal = money(order.subtotal) + add_subtotal
    new_packing = money(order.packing_fee) + add_packing
    # 会员折扣：POS 订单无顾客则无折扣；有顾客则按原折扣率重算
    from app.services.customer_service import CustomerService
    rate = await CustomerService.resolve_member_discount_rate(db, order.customer_id)
    old_discountable = order.subtotal - order.discount_amount  # 已下单的可折扣小计
    new_discountable = old_discountable + add_discountable
    discount_amount, base_total = CustomerService.calc_member_discount(
        new_subtotal, new_packing, rate, discountable_subtotal=new_discountable
    )
    order.subtotal = new_subtotal
    order.discount_amount = discount_amount
    order.packing_fee = new_packing
    order.total_amount = money(base_total)
    await db.flush()

    table = await db.get(DiningTable, order.table_id) if order.table_id else None
    await ws_manager.notify_order_event(
        "ORDER_UPDATED",
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "status": order.status,
            "store_id": order.store_id,
            "table_id": order.table_id,
            "table_no": table.table_no if table else None,
            "action": "ADD_ITEMS",
        },
    )
    return success(
        {
            "id": order.id,
            "order_no": order.order_no,
            "total_amount": str(order.total_amount),
            "subtotal": str(order.subtotal),
            "discount_amount": str(order.discount_amount),
            "packing_fee": str(order.packing_fee),
            "status": order.status,
            "payment_status": order.payment_status,
            "table": _table_payload(table),
        },
        request=request,
    )


@router.post("/{order_id}/transfer-table", dependencies=[require_pos()])
async def transfer_table(
    order_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    new_table_id: int = Query(..., description="新桌台 ID"),
):
    """转台：将订单从当前桌号转到新桌号，记录转台历史。"""
    order = await db.get(Order, order_id)
    if not order:
        raise AppException(40404, "订单不存在")
    assert_order_store_access(user, order)
    if order.channel != "POS":
        raise AppException(40009, "仅收银台订单可转台")
    if order.status not in ("PENDING_PAYMENT", "PAID", "CONFIRMED", "PREPARING", "COOKING"):
        raise AppException(40009, f"订单当前状态不可转台: {order.status}")

    new_table = await db.get(DiningTable, int(new_table_id))
    if not new_table or new_table.status != 1:
        raise AppException(40403, "目标桌台不存在或已停用")
    assert_store_access(user, new_table.store_id)
    if int(new_table.store_id) != int(order.store_id):
        raise AppException(40002, "不可跨门店转台")

    old_table_id = order.table_id
    old_table = await db.get(DiningTable, old_table_id) if old_table_id else None
    old_table_no = old_table.table_no if old_table else None

    order.table_id = int(new_table_id)
    await db.flush()

    # 记录转台历史到订单状态历史
    from app.services.status_history import record_order_status
    await record_order_status(
        db,
        order_id=order.id,
        from_status=order.status,
        to_status=order.status,
        source="TABLE_TRANSFER",
        operator_id=user.id,
        remark=f"转台: {old_table_no or '散客'} → {new_table.table_no}",
    )

    await ws_manager.notify_order_event(
        "TABLE_TRANSFER",
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "store_id": order.store_id,
            "old_table_id": old_table_id,
            "old_table_no": old_table_no,
            "new_table_id": new_table.id,
            "new_table_no": new_table.table_no,
        },
    )
    return success(
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "old_table_id": old_table_id,
            "old_table_no": old_table_no,
            "new_table_id": new_table.id,
            "new_table_no": new_table.table_no,
        },
        request=request,
    )


@router.get("/pos/pending", dependencies=[require_pos()])
async def list_pos_pending_orders(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
):
    """收银台：列出本门店所有未结算（PENDING_PAYMENT）的 POS 订单，按桌号分组。"""
    sid = require_bound_store(user, store_id)
    q = (
        select(Order)
        .where(
            Order.status == "PENDING_PAYMENT",
            Order.channel == "POS",
            Order.store_id == int(sid),
        )
        .order_by(Order.id)
    )
    result = await db.execute(q)
    orders_list = list(result.scalars().all())
    if not orders_list:
        return success([], request=request)

    order_ids = [o.id for o in orders_list]
    items = list(
        (await db.execute(select(OrderItem).where(OrderItem.order_id.in_(order_ids)))).scalars().all()
    )
    items_by_order: dict[int, list] = {}
    for it in items:
        items_by_order.setdefault(it.order_id, []).append(it)

    table_ids = [o.table_id for o in orders_list if o.table_id]
    table_map = await _load_tables(db, table_ids) if table_ids else {}

    result_list = []
    for o in orders_list:
        o_items = items_by_order.get(o.id, [])
        tbl = table_map.get(o.table_id) if o.table_id else None
        result_list.append({
            "id": o.id,
            "order_no": o.order_no,
            "table_id": o.table_id,
            "table_no": tbl.table_no if tbl else None,
            "table_name": tbl.table_name if tbl else None,
            "dining_type": o.dining_type,
            "total_amount": str(o.total_amount),
            "subtotal": str(o.subtotal),
            "item_count": len(o_items),
            "ordered_at": ensure_local_aware(o.ordered_at).isoformat() if o.ordered_at else None,
            "remark": o.remark,
        })
    return success(result_list, request=request)


@router.get("", dependencies=[require_permission("order:read")])
async def list_orders(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    payment_status: str | None = None,
    store_id: int | None = None,
    keyword: str | None = None,
    channel: str | None = None,
    dining_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """订单列表。keyword 匹配订单号/桌号；date_* 按下单 created_at。"""
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
    if channel:
        q = q.where(Order.channel == channel.strip().upper())
    if dining_type:
        dt = dining_type.strip().upper()
        if dt in ("DINE_IN", "TAKEAWAY"):
            q = q.where(Order.dining_type == dt)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = q.outerjoin(DiningTable, DiningTable.id == Order.table_id).where(
            or_(
                Order.order_no.like(kw),
                DiningTable.table_no.like(kw),
                DiningTable.table_name.like(kw),
            )
        )
    q = _apply_date_range(q, Order.created_at, date_from, date_to)

    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    result = await db.execute(q.order_by(Order.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    order_rows = list(result.scalars().all())
    table_map = await _load_tables(db, [o.table_id for o in order_rows if o.table_id])
    store_map = await _load_stores(db, [o.store_id for o in order_rows if o.store_id])
    from app.models import Customer
    from app.services.customer_service import CustomerService

    cust_ids = list({o.customer_id for o in order_rows if o.customer_id})
    cust_map: dict[int, Customer] = {}
    if cust_ids:
        cust_map = {
            c.id: c
            for c in (await db.execute(select(Customer).where(Customer.id.in_(cust_ids)))).scalars().all()
        }
    level_map = await CustomerService.load_levels_map(
        db, [c.member_level_id for c in cust_map.values()]
    )

    items = []
    for o in order_rows:
        t = table_map.get(o.table_id) if o.table_id else None
        store = store_map.get(o.store_id)
        cust = cust_map.get(o.customer_id) if o.customer_id else None
        level = level_map.get(cust.member_level_id) if cust and cust.member_level_id else None
        member = CustomerService.member_payload(cust, level) if cust else {
            "is_member": False,
            "member_level_id": None,
            "member_level_name": None,
            "member_discount_rate": None,
            "member_min_points": None,
        }
        items.append(
            {
                "id": o.id,
                "order_no": o.order_no,
                "store_id": o.store_id,
                "store_name": store.name if store else None,
                "store_no": store.store_no if store else None,
                "customer_id": o.customer_id,
                "customer_no": cust.customer_no if cust else None,
                "customer_name": (cust.display_name or cust.nickname) if cust else None,
                "customer_phone": cust.phone if cust else None,
                **member,
                "table_id": o.table_id,
                "table_no": t.table_no if t else None,
                "table_name": t.table_name if t else None,
                "dining_type": o.dining_type,
                "subtotal": str(o.subtotal),
                "discount_amount": str(o.discount_amount),
                "coupon_discount": str(o.coupon_discount or "0.00"),
                "voucher_discount": str(o.voucher_discount or "0.00"),
                "packing_fee": str(o.packing_fee),
                "status": o.status,
                "channel": o.channel,
                "total_amount": str(o.total_amount),
                "payment_status": o.payment_status,
                "created_at": o.created_at.isoformat() if o.created_at else None,
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
async def kitchen_orders(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
    limit: int = 200,
):
    """厨房 KDS：批量加载明细/规格/外卖信息，消除 N+1；按门店隔离。

    limit 上限 500，防止全量未完结订单撑爆内存。
    """
    from app.core.response import now_local
    from app.models import Store
    from app.services.store_scope import require_bound_store

    effective_store = require_bound_store(user, store_id)
    cap = min(max(1, int(limit or 200)), 500)
    q = select(Order).where(Order.status.in_(["PENDING_PAYMENT", "PAID", "CONFIRMED", "PREPARING", "COOKING", "READY"]))
    if effective_store:
        q = q.where(Order.store_id == effective_store)
    result = await db.execute(
        q.order_by(Order.paid_at.is_(None), Order.paid_at, Order.id).limit(cap)
    )
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
    product_ids = list({it.product_id for it in items if it.product_id})
    product_flags: dict[int, bool] = {}
    if product_ids:
        product_flags = {
            int(p.id): bool(p.no_cooking)
            for p in (await db.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()
        }
    table_map = await _load_tables(db, [o.table_id for o in order_list if o.table_id])
    store_ids = list({o.store_id for o in order_list if o.store_id})
    store_map = {}
    if store_ids:
        store_map = {
            s.id: s
            for s in (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars().all()
        }
    from app.models import Customer
    from app.services.customer_service import CustomerService

    cust_ids = list({o.customer_id for o in order_list if o.customer_id})
    cust_map: dict[int, Customer] = {}
    if cust_ids:
        cust_map = {
            c.id: c
            for c in (await db.execute(select(Customer).where(Customer.id.in_(cust_ids)))).scalars().all()
        }
    level_map = await CustomerService.load_levels_map(
        db, [c.member_level_id for c in cust_map.values() if c.member_level_id]
    )

    from app.models import User

    claimer_ids = list({o.kitchen_claimed_by for o in order_list if o.kitchen_claimed_by})
    claimer_map: dict[int, User] = {}
    if claimer_ids:
        claimer_map = {
            u.id: u
            for u in (await db.execute(select(User).where(User.id.in_(claimer_ids)))).scalars().all()
        }

    channel_label_map = {
        "WECHAT": "微信堂食",
        "ALIPAY": "支付宝堂食",
        "MEITUAN": "美团外卖",
        "ELEME": "饿了么",
        "H5": "H5点餐",
        "POS": "收银POS",
    }
    now = now_local()
    orders = []
    for order in order_list:
        order_items = []
        for item in items_by_order.get(order.id, []):
            no_cooking = product_flags.get(int(item.product_id), False)
            order_items.append(
                {
                    "id": item.id,
                    "product_name": item.product_name_snapshot,
                    "quantity": item.quantity,
                    "options": [o.option_name_snapshot for o in opts_by_item.get(item.id, [])],
                    "remark": item.remark,
                    "no_cooking": no_cooking,
                }
            )
        all_no_cooking = bool(order_items) and all(i["no_cooking"] for i in order_items)
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
        anchor = ensure_local_aware(order.paid_at or order.ordered_at or order.created_at)
        wait_seconds = 0
        if anchor is not None:
            wait_seconds = max(0, int((now - anchor).total_seconds()))
        packing = float(order.packing_fee or 0)
        store = store_map.get(order.store_id)
        cust = cust_map.get(order.customer_id) if order.customer_id else None
        level = level_map.get(cust.member_level_id) if cust and cust.member_level_id else None
        member = CustomerService.member_payload(cust, level) if cust else {
            "is_member": False,
            "member_level_name": None,
        }
        claimer = claimer_map.get(order.kitchen_claimed_by) if order.kitchen_claimed_by else None
        claimer_name = None
        if claimer:
            claimer_name = (claimer.display_name or claimer.username or "").strip() or f"#{claimer.id}"
        orders.append(
            {
                "id": order.id,
                "order_no": order.order_no,
                "table_id": order.table_id,
                "table_no": t.table_no if t else None,
                "table_name": t.table_name if t else None,
                "dining_type": order.dining_type,
                "packing_fee": str(order.packing_fee or "0.00"),
                "discount_amount": str(order.discount_amount or "0.00"),
                "need_pack": (order.dining_type or "") == "TAKEAWAY" or packing > 0,
                "remark": order.remark,
                "store_id": order.store_id,
                "store_name": store.name if store else None,
                "store_no": store.store_no if store else None,
                "is_member": member.get("is_member", False),
                "member_level_name": member.get("member_level_name"),
                "status": order.status,
                "channel": order.channel,
                "channel_label": channel_label_map.get(order.channel or "", order.channel or "堂食"),
                "is_delivery": order.channel in ("MEITUAN", "ELEME"),
                "paid_at": order.paid_at.isoformat() if order.paid_at else None,
                "ordered_at": order.ordered_at.isoformat() if order.ordered_at else None,
                "wait_seconds": wait_seconds,
                "delivery": delivery_info,
                "items": order_items,
                "all_no_cooking": all_no_cooking,
                "kitchen_claimed_by": order.kitchen_claimed_by,
                "kitchen_claimed_by_name": claimer_name,
                "kitchen_claimed_at": order.kitchen_claimed_at.isoformat() if order.kitchen_claimed_at else None,
                "kitchen_claimed_by_me": bool(
                    order.kitchen_claimed_by and user.id and int(order.kitchen_claimed_by) == int(user.id)
                ),
            }
        )
    return success(orders, request=request)


def _verify_display_token(token: str | None) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    expected = (settings.display_screen_token or "").strip()
    if settings.is_production and not expected:
        raise HTTPException(status_code=503, detail="未配置 DISPLAY_SCREEN_TOKEN")
    if expected and (token or "").strip() != expected:
        raise HTTPException(status_code=403, detail="叫号屏访问令牌无效")


@router.get("/display/active")
async def display_active_orders(
    request: Request,
    db: DbSession,
    store_id: int = Query(..., ge=1, description="门店 ID"),
    token: str | None = Query(default=None, description="叫号屏令牌（与 DISPLAY_SCREEN_TOKEN 一致）"),
    limit: int = Query(default=200, ge=1, le=500, description="最多返回订单数（防止积压撑爆）"),
):
    """顾客叫号屏：仅展示本店堂食/自取订单状态，不含明细与操作。"""
    from app.services.customer_display import (
        DISPLAY_ACTIVE_STATUSES,
        PREPARING_STATUSES,
        build_display_item,
        is_in_store_order,
        load_ready_at_map,
    )

    _verify_display_token(token)
    store = await db.get(Store, int(store_id))
    if not store or int(store.status) != 1:
        raise HTTPException(status_code=404, detail="门店不存在或已停业")

    cap = min(max(1, int(limit or 200)), 500)
    q = (
        select(Order)
        .where(
            Order.store_id == int(store_id),
            Order.status.in_(DISPLAY_ACTIVE_STATUSES),
        )
        .order_by(Order.paid_at.is_(None), Order.paid_at, Order.id)
        .limit(cap)
    )
    order_list = [o for o in (await db.execute(q)).scalars().all() if is_in_store_order(o)]
    table_map = await _load_tables(db, [o.table_id for o in order_list if o.table_id])
    ready_ids = [o.id for o in order_list if o.status == "READY"]
    ready_at_map = await load_ready_at_map(db, ready_ids)

    preparing = []
    ready = []
    for order in order_list:
        ready_at = ready_at_map.get(order.id) if order.status == "READY" else None
        item = build_display_item(
            order,
            table_map.get(order.table_id) if order.table_id else None,
            ready_at=ready_at,
        )
        if order.status in PREPARING_STATUSES:
            preparing.append(item)
        else:
            ready.append(item)

    return success(
        {
            "store": {"id": store.id, "name": store.name, "store_no": store.store_no},
            "preparing": preparing,
            "ready": ready,
            "updated_at": now_local().isoformat(),
        },
        request=request,
    )


@router.get("/mine")
async def list_my_orders(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    page: int = 1,
    page_size: int = 20,
    store_id: int | None = None,
):
    """顾客历史订单列表（会员中心）。传 store_id 时仅返回该门店订单。"""
    from app.services.order_service import ORDER_STATUS_LABEL, PAYMENT_STATUS_LABEL

    page = max(1, page)
    page_size = min(max(1, page_size), 50)
    base = select(Order).where(Order.customer_id == customer.id)
    if store_id is not None and int(store_id) > 0:
        base = base.where(Order.store_id == int(store_id))
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
    store_map = await _load_stores(db, [o.store_id for o in rows if o.store_id])

    payload = []
    for o in rows:
        o_items = items_by_order.get(o.id, [])
        names = [i.product_name_snapshot for i in o_items[:3]]
        if len(o_items) > 3:
            names.append(f"等{len(o_items)}件")
        t = table_map.get(o.table_id) if o.table_id else None
        store = store_map.get(o.store_id) if o.store_id else None
        from app.services.customer_display import pickup_fields

        pickup = pickup_fields(o, t)
        payload.append(
            {
                "id": o.id,
                "order_no": o.order_no,
                "store_id": o.store_id,
                "store_name": store.name if store else None,
                "store_no": store.store_no if store else None,
                "status": o.status,
                "status_label": ORDER_STATUS_LABEL.get(o.status or "", o.status or ""),
                "payment_status": o.payment_status,
                "payment_status_label": PAYMENT_STATUS_LABEL.get(o.payment_status or "", o.payment_status or ""),
                "channel": o.channel,
                "dining_type": o.dining_type,
                "subtotal": str(o.subtotal),
                "discount_amount": str(o.discount_amount),
                "coupon_discount": str(o.coupon_discount or "0.00"),
                "voucher_discount": str(o.voucher_discount or "0.00"),
                "total_amount": str(o.total_amount),
                "item_count": len(o_items),
                "item_summary": "、".join(names) if names else "（无明细）",
                "table": _table_payload(t),
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "can_reorder": o.status not in ("PENDING_PAYMENT",) and len(o_items) > 0,
                **pickup,
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
        from app.api.deps import get_user_permissions
        from app.services.store_scope import assert_order_store_access

        perms = await get_user_permissions(db, user.role_id)
        if user.role.code != "SUPER_ADMIN" and "order:read" not in perms:
            raise HTTPException(status_code=403, detail="缺少权限: order:read")
        assert_order_store_access(user, order)

    if customer is not None:
        from app.services.order_access import assert_customer_owns_order, assert_dining_session_owner

        assert_customer_owns_order(order, customer.id, action="查看")
        await assert_dining_session_owner(db, order, customer.id, action="查看")

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
    from app.models import Customer
    from app.services.customer_display import pickup_fields
    from app.services.customer_service import CustomerService

    table_obj = await db.get(DiningTable, order.table_id) if order.table_id else None
    pickup = pickup_fields(order, table_obj)

    cust = await db.get(Customer, order.customer_id) if order.customer_id else None
    level = None
    if cust and cust.member_level_id:
        level = (await CustomerService.load_levels_map(db, [cust.member_level_id])).get(cust.member_level_id)
    member = (
        CustomerService.member_payload(cust, level)
        if cust
        else {
            "is_member": False,
            "member_level_id": None,
            "member_level_name": None,
            "member_discount_rate": None,
            "member_min_points": None,
        }
    )

    return success(

        {

            "id": order.id,

            "order_no": order.order_no,

            "store_id": order.store_id,

            "store_name": store.name if store else None,

            "store_no": store.store_no if store else None,

            "status": order.status,

            "status_label": ORDER_STATUS_LABEL.get(order.status or "", order.status or ""),

            "payment_status": order.payment_status,

            "payment_status_label": PAYMENT_STATUS_LABEL.get(order.payment_status or "", order.payment_status or ""),

            "channel": order.channel,

            "subtotal": str(order.subtotal),

            "discount_amount": str(order.discount_amount),

            "coupon_discount": str(order.coupon_discount or "0.00"),
            "voucher_discount": str(order.voucher_discount or "0.00"),

            "total_amount": str(order.total_amount),

            "table_id": order.table_id,

            "table": _table_payload(table_obj),

            "dining_type": order.dining_type,

            "packing_fee": str(order.packing_fee),

            "remark": order.remark,

            "customer_id": order.customer_id,

            "customer_no": cust.customer_no if cust else None,

            "customer_name": (cust.display_name or cust.nickname) if cust else None,

            "customer_phone": cust.phone if cust else None,

            **member,

            "created_at": order.created_at.isoformat() if order.created_at else None,

            "items": items,

            "can_reorder": bool(items) and order.status not in ("PENDING_PAYMENT",),

            **pickup,

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
    old_status = (existing.status or "").strip().upper()
    order = await OrderCreateService.update_status(
        db,
        order_id,
        data.status,
        operator_id=user.id,
        force_takeover=bool(data.force_takeover),
    )

    await DeliveryService.sync_kitchen_status(db, order)

    target = (data.status or "").strip().upper()
    if target == "PREPARING" and old_status in ("PAID", "CONFIRMED"):
        from app.services.order_lifecycle import order_all_no_cooking
        from app.services.print_tasks import schedule_print

        if not await order_all_no_cooking(db, order.id):
            schedule_print(order.id)

    await ws_manager.notify_order_event(
        f"ORDER_{data.status}",
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "status": order.status,
            "channel": order.channel,
            "store_id": order.store_id,
            "table_id": order.table_id,
            "kitchen_claimed_by": order.kitchen_claimed_by,
        },
    )

    return success(
        {
            "id": order.id,
            "status": order.status,
            "kitchen_claimed_by": order.kitchen_claimed_by,
        },
        request=request,
    )


@router.post("/{order_id}/kitchen-takeover", dependencies=[require_kitchen()])
async def kitchen_takeover(order_id: int, request: Request, db: DbSession, user: CurrentUser):
    """接管已被他人认领的厨房单（不改状态）。"""
    from app.services.kitchen_claim import claimer_display_name, takeover_kitchen_claim
    from app.services.store_scope import assert_order_store_access

    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    assert_order_store_access(user, order)
    prev = order.kitchen_claimed_by
    prev_name = await claimer_display_name(db, prev) if prev else ""
    order = await takeover_kitchen_claim(db, order, operator_id=user.id)
    await ws_manager.notify_order_event(
        "ORDER_KITCHEN_CLAIM",
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "status": order.status,
            "store_id": order.store_id,
            "table_id": order.table_id,
            "kitchen_claimed_by": order.kitchen_claimed_by,
            "previous_claimed_by": prev,
        },
    )
    my_name = await claimer_display_name(db, user.id)
    return success(
        {
            "id": order.id,
            "status": order.status,
            "kitchen_claimed_by": order.kitchen_claimed_by,
            "kitchen_claimed_by_name": my_name,
            "previous_claimed_by_name": prev_name or None,
        },
        request=request,
    )


@router.post("/{order_id}/print", dependencies=[require_kitchen()])
async def reprint_kitchen_ticket(order_id: int, request: Request, db: DbSession, user: CurrentUser):
    """厨房补打制作单（局域网 ESC/POS 或芯烨云）。"""
    from app.services.print_service import PrintService
    from app.services.store_scope import assert_order_store_access

    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    assert_order_store_access(user, order)
    from app.core.config import get_settings
    from app.services.system_settings_service import SystemSettingsService

    if not SystemSettingsService.resolved_bool_sync(
        "xpyun_enabled", get_settings().xpyun_enabled
    ):
        return success({"printed": False, "message": "打印未启用（厨房打印总开关关闭）"}, request=request)

    # 手动补打幂等：短时 Redis NX 锁防止快速重复点击导致重复出票
    # TTL=15s 足够覆盖一次打印往返；锁自然过期，无需手动释放
    dedup_key = f"snh:print:dedup:{order_id}:KITCHEN"
    try:
        r = await get_redis()
        acquired = await r.set(dedup_key, str(int(time.time())), nx=True, ex=15)
        if not acquired:
            return success(
                {"printed": False, "message": "正在打印中，请勿重复点击"},
                request=request,
            )
    except Exception:
        # Redis 不可用时降级放行（不阻断打印主流程）
        pass

    try:
        result = await PrintService.print_order(db, order_id)
    except Exception:
        # 先提交 PrintJob RETRYING/FAILED，避免 get_db 回滚冲掉
        await db.commit()
        raise
    if result is None:
        # 订单已取消等业务跳过（非配置缺失；配置缺失会抛 AppException）
        return success({"printed": False, "message": "当前订单状态不可打印"}, request=request)
    return success({"printed": True, "order_no": order.order_no, "result": result}, request=request)

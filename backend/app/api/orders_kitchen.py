"""厨房 KDS / 叫号屏：从 orders 路由拆出，路径仍挂在 /api/orders。"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_kitchen
from app.api.orders_common import load_tables
from app.core.exceptions import AppException
from app.core.response import now_local, serialize_dt, success
from app.models import Order, OrderItem, Store
from app.schemas.order import OrderStatusUpdate
from app.services.audit_service import AuditService
from app.services.customer_service import OrderCreateService
from app.services.delivery_service import DeliveryService
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/orders", tags=["订单"])

@router.get("/kitchen/stores", dependencies=[require_kitchen()])
async def kitchen_stores(request: Request, db: DbSession, user: CurrentUser):
    """KDS 门店切换列表：超管看全部营业门店；门店账号仅本店。"""
    from app.models import Store

    if user.role.code == "SUPER_ADMIN":
        rows = list((await db.execute(select(Store).where(Store.status == 1).order_by(Store.id))).scalars().all())
    else:
        if not user.store_id:
            raise AppException(40301, "账号未绑定门店", status_code=403)
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
    # P0 修复：KDS 不再展示 PENDING_PAYMENT 订单，避免厨房误接单未支付订单。
    q = select(Order).where(Order.status.in_(["PAID", "CONFIRMED", "PREPARING", "COOKING", "READY"]))
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
    table_map = await load_tables(db, [o.table_id for o in order_list if o.table_id])
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
        packing = money(order.packing_fee)
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
                "packing_fee": str(packing),
                "discount_amount": str(money(order.discount_amount)),
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
                "urge_count": order.urge_count or 0,
                "urged_at": serialize_dt(order.urged_at),
                "scheduled_pickup_at": (
                    serialize_dt(order.scheduled_pickup_at)
                ),
                "paid_at": serialize_dt(order.paid_at),
                "ordered_at": serialize_dt(order.ordered_at),
                "wait_seconds": wait_seconds,
                "delivery": delivery_info,
                "items": order_items,
                "all_no_cooking": all_no_cooking,
                "kitchen_claimed_by": order.kitchen_claimed_by,
                "kitchen_claimed_by_name": claimer_name,
                "kitchen_claimed_at": serialize_dt(order.kitchen_claimed_at),
                "kitchen_claimed_by_me": bool(
                    order.kitchen_claimed_by and user.id and int(order.kitchen_claimed_by) == int(user.id)
                ),
            }
        )
    return success(orders, request=request)


def verify_display_token(token: str | None, *, expected: str, is_production: bool) -> None:
    expected = (expected or "").strip()
    if is_production and not expected:
        raise AppException(50301, "未配置叫号屏访问令牌", status_code=503)
    if expected and (token or "").strip() != expected:
        raise AppException(40301, "叫号屏访问令牌无效", status_code=403)


@router.get("/display/active")
async def display_active_orders(
    request: Request,
    db: DbSession,
    store_id: int = Query(..., ge=1, description="门店 ID"),
    token: str | None = Query(default=None, description="叫号屏令牌（本店或平台 DISPLAY_SCREEN_TOKEN）"),
    limit: int = Query(default=200, ge=1, le=500, description="最多返回订单数（防止积压撑爆）"),
):
    """顾客叫号屏：本店堂食/自取订单状态 + 餐品摘要（不含顾客联系方式与操作）。"""
    from app.core.config import get_settings
    from app.services.customer_display import (
        DISPLAY_ACTIVE_STATUSES,
        PREPARING_STATUSES,
        build_display_item,
        is_customer_display_enabled,
        is_in_store_order,
        load_ready_at_map,
    )
    from app.services.system_settings_service import SystemSettingsService

    if not await is_customer_display_enabled(db, int(store_id)):
        raise AppException(
            40301,
            "本店未开通大厅叫号屏，请在门店 → 门店信息 → 业务参数 → 订单与厨房中开启",
            status_code=403,
        )

    settings = get_settings()
    global_token = (settings.display_screen_token or "").strip()
    store_tok = await SystemSettingsService.effective_setting(
        db, "display_screen_token", store_id=int(store_id), fallback=global_token
    )
    expected = (store_tok or "").strip() or global_token
    verify_display_token(token, expected=expected, is_production=settings.is_production)
    store = await db.get(Store, int(store_id))
    if not store or int(store.status) != 1:
        raise AppException(40404, "门店不存在或已停业", status_code=404)

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
    table_map = await load_tables(db, [o.table_id for o in order_list if o.table_id])
    ready_ids = [o.id for o in order_list if o.status == "READY"]
    ready_at_map = await load_ready_at_map(db, ready_ids)
    order_ids = [o.id for o in order_list]
    items_by_order: dict[int, list[OrderItem]] = {}
    if order_ids:
        item_rows = list(
            (
                await db.execute(
                    select(OrderItem).where(OrderItem.order_id.in_(order_ids)).order_by(OrderItem.id)
                )
            ).scalars()
        )
        for it in item_rows:
            items_by_order.setdefault(int(it.order_id), []).append(it)

    preparing = []
    ready = []
    for order in order_list:
        ready_at = ready_at_map.get(order.id) if order.status == "READY" else None
        item = build_display_item(
            order,
            table_map.get(order.table_id) if order.table_id else None,
            ready_at=ready_at,
            items=items_by_order.get(int(order.id), []),
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
            "updated_at": serialize_dt(now_local()),
        },
        request=request,
    )


@router.patch("/{order_id}/status", dependencies=[require_kitchen()])
async def update_order_status(order_id: int, data: OrderStatusUpdate, request: Request, db: DbSession, user: CurrentUser):
    from app.services.store_scope import assert_order_store_access
    existing = await db.get(Order, order_id)
    if not existing:
        raise AppException(40404, "订单不存在", status_code=404)
    assert_order_store_access(user, existing)
    old_status = (existing.status or "").strip().upper()
    order = await OrderCreateService.update_status(
        db,
        order_id,
        data.status,
        operator_id=user.id,
        force_takeover=bool(data.force_takeover),
    )

    target = (data.status or "").strip().upper()
    if target == "PREPARING" and old_status in ("PAID", "CONFIRMED"):
        from app.services.order_lifecycle import order_all_no_cooking
        from app.services.print_tasks import schedule_print

        if not await order_all_no_cooking(db, order.id):
            schedule_print(order.id)

    # 先提交释放 Order 行锁，再调美团/饿了么同步，避免持锁等平台 HTTP
    await db.commit()
    await DeliveryService.sync_kitchen_status(db, order)
    await db.commit()
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

    await AuditService.log_staff(
        db,
        user=user,
        action="order.update_status",
        module="order",
        target_type="order",
        target_id=order.id,
        detail={
            "order_no": order.order_no,
            "store_id": int(order.store_id) if order.store_id else None,
            "from_status": old_status,
            "to_status": (data.status or "").strip().upper(),
        },
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
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
        raise AppException(40404, "订单不存在", status_code=404)
    assert_order_store_access(user, order)
    prev = order.kitchen_claimed_by
    prev_name = await claimer_display_name(db, prev) if prev else ""
    order = await takeover_kitchen_claim(db, order, operator_id=user.id)
    await db.commit()
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
    await AuditService.log_staff(
        db,
        user=user,
        action="order.kitchen_takeover",
        module="order",
        target_type="order",
        target_id=order.id,
        detail={
            "order_no": order.order_no,
            "store_id": int(order.store_id) if order.store_id else None,
            "previous_claimed_by": prev,
            "new_claimed_by": int(user.id),
        },
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
    )
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
        raise AppException(40404, "订单不存在", status_code=404)
    assert_order_store_access(user, order)
    from app.core.config import get_settings
    from app.services.system_settings_service import SystemSettingsService

    # 门店覆盖优先；禁止 sync 全局读绕过 store_settings
    print_on = await SystemSettingsService.effective_bool(
        db,
        "xpyun_enabled",
        get_settings().xpyun_enabled,
        store_id=int(order.store_id) if order.store_id else None,
    )
    if not print_on:
        return success({"printed": False, "message": "打印未启用（厨房打印总开关关闭）"}, request=request)

    # 手动补打幂等：Redis 加速 + MySQL processed_messages 事实源
    # 短窗内重复点击直接丢弃，避免网络抖动导致重复出票
    from app.domains.messaging.idempotency import release as release_msg
    from app.domains.messaging.idempotency import try_acquire as claim_msg

    print_msg_id = f"reprint:kitchen:{order_id}"
    acquired = await claim_msg(
        db,
        scope="print",
        message_id=print_msg_id,
        ttl_seconds=30,
        meta={"order_id": order_id, "ticket": "KITCHEN"},
    )
    if not acquired:
        return success(
            {"printed": False, "message": "正在打印中或刚打印过，请勿重复点击"},
            request=request,
        )

    try:
        result = await PrintService.print_order(db, order_id)
    except Exception:
        # 先提交 PrintJob RETRYING/FAILED，避免 get_db 回滚冲掉
        await release_msg(db, scope="print", message_id=print_msg_id)
        await db.commit()
        raise
    if result is None:
        await release_msg(db, scope="print", message_id=print_msg_id)
        # 订单已取消等业务跳过（非配置缺失；配置缺失会抛 AppException）
        return success({"printed": False, "message": "当前订单状态不可打印"}, request=request)
    await AuditService.log_staff(
        db,
        user=user,
        action="order.print",
        module="order",
        target_type="order",
        target_id=int(order_id),
        detail={
            "order_no": order.order_no,
            "store_id": int(order.store_id) if order.store_id else None,
            "printed": True,
        },
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
    )
    return success({"printed": True, "order_no": order.order_no, "result": result}, request=request)

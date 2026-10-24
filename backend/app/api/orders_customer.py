"""顾客自助：我的订单、续单、催单、领券、自助退款。从 orders 拆出，路径仍挂在 /api/orders。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.deps import CurrentCustomer, DbSession
from app.api.orders_common import load_stores, load_tables, table_payload
from app.core.exceptions import AppException
from app.core.redis_client import get_redis
from app.core.response import calc_pages, now_local, serialize_dt, success, success_no_store
from app.models import Order, OrderItem
from app.services.audit_service import AuditService
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/orders", tags=["订单"])

_URGE_ALLOWED_STATUSES = {"CONFIRMED", "PREPARING", "COOKING"}


async def get_customer_order_or_404(db, order_id: int, customer_id: int) -> Order:
    order = await db.get(Order, int(order_id))
    if not order or order.customer_id != int(customer_id):
        # 他人订单不暴露存在性
        raise AppException(40404, "订单不存在")
    return order


@router.get("/mine")
async def list_my_orders(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    page: int = 1,
    page_size: int = 20,
    store_id: int | None = None,
    status: str | None = None,
):
    """顾客历史订单列表（会员中心）。传 store_id 时仅返回该门店订单；status 支持逗号分隔多状态。"""
    from app.services.order_service import ORDER_STATUS_LABEL, PAYMENT_STATUS_LABEL

    page = max(1, page)
    page_size = min(max(1, page_size), 50)
    base = select(Order).where(Order.customer_id == customer.id)
    if store_id is not None and int(store_id) > 0:
        base = base.where(Order.store_id == int(store_id))
    if status and status.strip():
        # 白名单过滤非法状态；小程序 tab 按业务分组传多状态（如进行中=PAID,CONFIRMED,...）
        statuses = [s.strip().upper() for s in status.split(",") if s.strip() in ORDER_STATUS_LABEL]
        if statuses:
            base = base.where(Order.status.in_(statuses))
        else:
            base = base.where(Order.id.is_(None))  # 全部非法时不得放宽为全量
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = list(
        (
            await db.execute(
                base.order_by(Order.created_at.desc(), Order.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
    )
    if not rows:
        return success_no_store(
            {"items": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0},
            request=request,
        )

    order_ids = [o.id for o in rows]
    items = list((await db.execute(select(OrderItem).where(OrderItem.order_id.in_(order_ids)))).scalars())
    items_by_order: dict[int, list[OrderItem]] = {}
    for it in items:
        items_by_order.setdefault(it.order_id, []).append(it)
    table_map = await load_tables(db, [o.table_id for o in rows if o.table_id])
    store_map = await load_stores(db, [o.store_id for o in rows if o.store_id])
    from app.services.customer_display import is_customer_display_enabled, pickup_fields

    display_flag_cache: dict[int, bool] = {}

    payload = []
    for o in rows:
        o_items = items_by_order.get(o.id, [])
        names = [i.product_name_snapshot for i in o_items[:3]]
        if len(o_items) > 3:
            names.append(f"等{len(o_items)}件")
        t = table_map.get(o.table_id) if o.table_id else None
        store = store_map.get(o.store_id) if o.store_id else None
        sid = int(o.store_id) if o.store_id else None
        if sid is not None and sid not in display_flag_cache:
            display_flag_cache[sid] = await is_customer_display_enabled(db, sid)
        has_display = display_flag_cache.get(sid or 0, False) if sid else False
        pickup = pickup_fields(o, t, has_customer_display=has_display)
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
                "table": table_payload(t),
                "created_at": serialize_dt(o.created_at),
                "urge_count": o.urge_count or 0,
                "urged_at": serialize_dt(o.urged_at),
                "scheduled_pickup_at": (
                    serialize_dt(o.scheduled_pickup_at)
                ),
                "can_reorder": o.status not in ("PENDING_PAYMENT",) and len(o_items) > 0,
                **pickup,
            }
        )

    return success_no_store(
        {
            "items": payload,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
        },
        request=request,
    )


@router.post("/{order_id}/reorder")
async def reorder_order(order_id: int, request: Request, db: DbSession, customer: CurrentCustomer):
    """根据历史订单生成可再次下单的购物车条目（不直接创建新订单，便于用户确认）"""
    from app.services.order_service import OrderReorderService

    order = await db.get(Order, order_id)
    if not order:
        raise AppException(40404, "订单不存在", status_code=404)
    if order.customer_id != customer.id:
        raise AppException(40301, "无权复购此订单", status_code=403)
    if order.status == "PENDING_PAYMENT":
        raise AppException(40042, "待支付订单请先完成支付或取消后再复购")

    data = await OrderReorderService.build_cart_from_order(db, order)
    await AuditService.log(
        db,
        action="order.reorder",
        module="order",
        user_id=None,
        target_type="order",
        target_id=order.id,
        detail={
            "order_no": order.order_no,
            "store_id": int(order.store_id) if order.store_id else None,
            "customer_id": int(customer.id),
        },
        request=request,
        category=AuditService.CATEGORY_CUSTOMER,
        store_id=int(order.store_id) if order.store_id else None,
    )
    return success(data, request=request)


@router.post("/{order_id}/urge")
async def urge_order(order_id: int, request: Request, db: DbSession, customer: CurrentCustomer):
    """顾客自助催单：限 CONFIRMED/PREPARING/COOKING；5 分钟限频（Redis 故障 fail-open）。"""
    order = await get_customer_order_or_404(db, order_id, customer.id)
    if order.status not in _URGE_ALLOWED_STATUSES:
        raise AppException(40058, "当前订单状态无法催单")
    from app.core.cache_keys import order_urge_key

    limit_key = order_urge_key(int(order.id))
    limited = True
    try:
        redis = await get_redis()
        limited = not await redis.set(limit_key, "1", ex=300, nx=True)
    except Exception:
        # Redis 不可用时放行（业务幂等：urge_count 只加 1 次，可接受重复催）
        limited = False
    if limited:
        return success(
            {
                "urge_count": order.urge_count or 0,
                "urged_at": serialize_dt(order.urged_at),
                "limited": True,
            },
            request=request,
        )
    order.urge_count = int(order.urge_count or 0) + 1
    order.urged_at = now_local()
    await db.commit()
    try:
        await ws_manager.notify_order_event(
            "ORDER_URGED",
            {
                "order_id": order.id,
                "order_no": order.order_no,
                "store_id": order.store_id,
                "urge_count": order.urge_count,
                "urged_at": serialize_dt(order.urged_at),
            },
        )
    except Exception:
        pass
    await AuditService.log(
        db,
        action="order.urge",
        module="order",
        user_id=None,
        target_type="order",
        target_id=int(order.id),
        detail={"order_no": order.order_no, "urge_count": order.urge_count, "by": "customer"},
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
    )
    return success(
        {
            "urge_count": order.urge_count,
            "urged_at": serialize_dt(order.urged_at),
            "limited": False,
        },
        request=request,
    )


@router.get("/{order_id}/claimable-coupon")
async def get_claimable_coupon(
    order_id: int, request: Request, db: DbSession, customer: CurrentCustomer
):
    """支付成功领券：返回该订单是否可领（pay_success_coupon_name 配置驱动）。"""
    from app.models import CustomerCoupon
    from app.services.coupon_service import CouponService

    order = await get_customer_order_or_404(db, order_id, customer.id)
    if order.payment_status != "PAID":
        return success({"available": False, "coupon_name": None, "claimed": False}, request=request)
    from app.services.system_settings_service import SystemSettingsService

    name = (await SystemSettingsService.get_value(db, "pay_success_coupon_name", "") or "").strip()
    if not name:
        return success({"available": False, "coupon_name": None, "claimed": False}, request=request)
    store_id = int(order.store_id) if order.store_id else None
    tmpl = await CouponService._welcome_template(db, name, store_id=store_id)
    if not tmpl:
        return success({"available": False, "coupon_name": name, "claimed": False}, request=request)

    pay_key = CouponService.pay_success_client_request_id(int(customer.id), int(tmpl.id))
    claimed = (
        await db.execute(
            select(CustomerCoupon.id).where(
                CustomerCoupon.customer_id == customer.id,
                CustomerCoupon.template_id == tmpl.id,
                CustomerCoupon.client_request_id == pay_key,
            ).limit(1)
        )
    ).scalar_one_or_none()
    return success(
        {
            "available": claimed is None,
            "coupon_name": tmpl.name,
            "discount_value": str(tmpl.discount_value),
            "coupon_type": tmpl.coupon_type,
            "claimed": claimed is not None,
        },
        request=request,
    )


@router.post("/{order_id}/claim-coupon")
async def claim_order_coupon(
    order_id: int, request: Request, db: DbSession, customer: CurrentCustomer
):
    """支付成功后领取活动券（幂等键 pay_success:{cid}:{tid}，与欢迎券隔离）。"""
    from app.services.coupon_service import CouponService

    order = await get_customer_order_or_404(db, order_id, customer.id)
    if order.payment_status != "PAID":
        raise AppException(40059, "订单支付完成后方可领券")
    from app.services.system_settings_service import SystemSettingsService

    name = (await SystemSettingsService.get_value(db, "pay_success_coupon_name", "") or "").strip()
    if not name:
        raise AppException(40060, "领券活动未开启")
    store_id = int(order.store_id) if order.store_id else None
    tmpl = await CouponService._welcome_template(db, name, store_id=store_id)
    if not tmpl:
        return success({"claimed": False, "coupon": None}, request=request)
    req_key = CouponService.pay_success_client_request_id(int(customer.id), int(tmpl.id))
    issued = await CouponService.issue_from_template(
        db,
        int(customer.id),
        name,
        store_id=store_id,
        client_request_id=req_key,
    )
    await db.commit()
    if not issued:
        # 已领过 / 模板不存在 / 库存空：幂等返回未领取
        return success({"claimed": False, "coupon": None}, request=request)
    return success(
        {
            "claimed": True,
            "coupon": {
                "id": issued.id,
                "coupon_no": issued.coupon_no,
                "name": name,
                "expires_at": serialize_dt(issued.expires_at),
            },
        },
        request=request,
    )


@router.post("/{order_id}/self-refund")
async def self_refund_order(
    order_id: int,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """顾客自助退款：已支付且后厨未开始制作的线上整单，创建退款申请走人工审批。"""
    from app.services.pos_refund_service import PosRefundService

    order = await get_customer_order_or_404(db, order_id, customer.id)
    row = await PosRefundService.create_customer_request(
        db, customer_id=customer.id, order_id=order.id, reason=None
    )
    await db.commit()
    await AuditService.log(
        db,
        action="order.self_refund_request",
        module="order",
        user_id=None,
        target_type="order",
        target_id=int(order.id),
        detail={"order_no": order.order_no, "request_id": row.id, "amount": str(row.amount)},
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
    )
    return success({"request_id": row.id, "status": row.status, "amount": str(row.amount)}, request=request)

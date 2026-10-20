from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, or_, select

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, StaffOrCustomer, require_permission
from app.core.exceptions import AppException
from app.core.response import apply_date_range as _apply_date_range, calc_pages, ensure_local_aware, success, success_no_store, serialize_dt
from app.models import Customer, DeliveryOrder, DiningTable, Order, OrderItem, OrderItemOption, Store
from app.schemas.order import OrderCreate
from app.services.audit_service import AuditService
from app.services.customer_service import CustomerService, OrderCreateService
from app.services.store_scope import assert_order_store_access, require_bound_store
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/orders", tags=["订单"])


from app.api.orders_common import load_stores as _load_stores
from app.api.orders_common import load_tables as _load_tables
from app.api.orders_common import table_payload as _table_payload
# 兼容测试与旧导入路径：厨房补打仍可从 orders 引用
from app.api.orders_kitchen import reprint_kitchen_ticket  # noqa: F401
from app.api.orders_pos import _recompute_pos_amounts_for_customer  # noqa: F401


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
        customer_coupon_id=None if data.skip_promotions else data.customer_coupon_id,
        customer_voucher_id=None if data.skip_promotions else data.customer_voucher_id,
        skip_promotions=bool(data.skip_promotions),
        operator_id=None,
        scheduled_pickup_at=data.scheduled_pickup_at,
        use_social_benefit=bool(data.use_social_benefit),
        social_rule_id=data.social_rule_id,
    )
    table = await db.get(DiningTable, order.table_id) if order.table_id else None
    # 一人结全桌：正式下单成功后清空本桌共享草稿
    try:
        from app.services.shared_cart_service import SharedCartService

        await SharedCartService.clear_after_order(
            db,
            store_id=int(order.store_id),
            table_id=int(order.table_id) if order.table_id else None,
            customer_id=int(customer.id),
        )
    except Exception:
        pass
    await db.commit()
    try:
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
    except Exception as ws_err:
        # WS 推送失败不应导致下单事务回滚（顾客已视为下单成功）
        import logging

        logging.getLogger("app.order").warning("下单 WS 通知失败 order=%s: %s", order.order_no, ws_err)
    await AuditService.log(
        db,
        action="order.create",
        module="order",
        user_id=None,
        target_type="order",
        target_id=order.id,
        detail={
            "order_no": order.order_no,
            "store_id": int(order.store_id) if order.store_id else None,
            "customer_id": int(order.customer_id) if order.customer_id else None,
            "channel": channel,
            "total_amount": str(order.total_amount),
            "table_id": int(order.table_id) if order.table_id else None,
        },
        request=request,
        category=AuditService.CATEGORY_CUSTOMER,
        store_id=int(order.store_id) if order.store_id else None,
    )
    from app.services.social_benefit_service import SocialBenefitService

    social_snap = await SocialBenefitService.order_social_snapshot(db, int(order.id))
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
            **social_snap,
        },
        request=request,
    )


@router.get("/operators", dependencies=[require_permission("order:read")])
async def list_order_operators(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
):
    """订单筛选用收银员下拉：仅返回实际下过单的员工，随门店隔离收窄。"""
    from app.models import User

    effective_store = require_bound_store(user, store_id)
    q = (
        select(User.id, User.username, User.display_name, User.status)
        .join(Order, Order.operator_id == User.id)
        .distinct()
    )
    if effective_store:
        q = q.where(Order.store_id == effective_store)
    rows = (await db.execute(q.order_by(User.id))).all()
    return success(
        [
            {
                "id": r[0],
                "username": r[1],
                "display_name": r[2],
                "name": r[2] or r[1],
                "status": r[3],
            }
            for r in rows
        ],
        request=request,
    )


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
    operator_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """订单列表。keyword 匹配订单号/桌号/顾客手机号·姓名·会员号；date_* 按下单 created_at。"""
    from app.models import Customer, User

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
    if operator_id:
        q = q.where(Order.operator_id == int(operator_id))
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = (
            q.outerjoin(DiningTable, DiningTable.id == Order.table_id)
            .outerjoin(Customer, Customer.id == Order.customer_id)
            .where(
                or_(
                    Order.order_no.like(kw),
                    DiningTable.table_no.like(kw),
                    DiningTable.table_name.like(kw),
                    Customer.phone.like(kw),
                    Customer.nickname.like(kw),
                    Customer.display_name.like(kw),
                    Customer.customer_no.like(kw),
                )
            )
        )
    q = _apply_date_range(q, Order.created_at, date_from, date_to)

    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    # created_at 同秒多单（高峰期常见），必须以 id 兜底，否则 OFFSET 翻页会重复/丢行
    result = await db.execute(
        q.order_by(Order.created_at.desc(), Order.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    order_rows = list(result.scalars().all())
    table_map = await _load_tables(db, [o.table_id for o in order_rows if o.table_id])
    store_map = await _load_stores(db, [o.store_id for o in order_rows if o.store_id])
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

    op_ids = list({o.operator_id for o in order_rows if o.operator_id})
    op_map: dict[int, User] = {}
    if op_ids:
        op_map = {
            u.id: u
            for u in (await db.execute(select(User).where(User.id.in_(op_ids)))).scalars().all()
        }

    from app.services.payment_service import PaymentService
    from app.services.social_benefit_service import SocialBenefitService

    order_ids = [int(o.id) for o in order_rows]
    social_map = await SocialBenefitService.orders_social_map(db, order_ids)
    tenders_map = await PaymentService.list_paid_tenders_map(db, order_ids)

    items = []
    for o in order_rows:
        t = table_map.get(o.table_id) if o.table_id else None
        store = store_map.get(o.store_id)
        cust = cust_map.get(o.customer_id) if o.customer_id else None
        op = op_map.get(o.operator_id) if o.operator_id else None
        level = level_map.get(cust.member_level_id) if cust and cust.member_level_id else None
        member = CustomerService.member_payload(cust, level) if cust else {
            "is_member": False,
            "member_level_id": None,
            "member_level_name": None,
            "member_discount_rate": None,
            "member_min_points": None,
        }
        social_snap = social_map.get(int(o.id)) or SocialBenefitService._empty_social_snapshot()
        tenders = tenders_map.get(int(o.id)) or []
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
                "payment_method": o.payment_method,
                "payment_tenders": [
                    {"provider": x.get("provider"), "amount": x.get("amount")} for x in tenders
                ],
                "paid_at": serialize_dt(o.paid_at) if o.paid_at else None,
                "operator_id": o.operator_id,
                "operator_name": (op.display_name or op.username) if op else None,
                "created_at": serialize_dt(o.created_at),
                **social_snap,
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


@router.get("/{order_id}")
async def get_order(order_id: int, request: Request, db: DbSession, auth: StaffOrCustomer):
    user, customer = auth
    order = await db.get(Order, order_id)
    if not order:
        raise AppException(40404, "订单不存在")
    if user is None and customer is None:
        raise AppException(40100, "未登录", status_code=401)
    if user is not None:
        from app.api.deps import get_user_permissions
        from app.services.store_scope import assert_order_store_access
        perms = await get_user_permissions(db, user.role_id)
        if user.role.code != "SUPER_ADMIN" and "order:read" not in perms:
            raise AppException(40300, "缺少权限: order:read", status_code=403)
        assert_order_store_access(user, order)
    if customer is not None:
        from app.services.order_access import assert_customer_owns_order, assert_dining_session_owner
        assert_customer_owns_order(order, customer.id, action="查看")
        await assert_dining_session_owner(db, order, customer.id, action="查看")
    from app.services.order_service import ORDER_STATUS_LABEL, PAYMENT_STATUS_LABEL
    items_rows = list(
        (
            await db.execute(
                select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id)
            )
        ).scalars().all()
    )
    item_ids = [int(i.id) for i in items_rows]
    opts_by_item: dict[int, list] = {iid: [] for iid in item_ids}
    if item_ids:
        for o in (
            await db.execute(
                select(OrderItemOption)
                .where(OrderItemOption.order_item_id.in_(item_ids))
                .order_by(OrderItemOption.id)
            )
        ).scalars().all():
            opts_by_item.setdefault(int(o.order_item_id), []).append(o)
    items = []
    for item in items_rows:
        opts = opts_by_item.get(int(item.id), [])
        items.append(
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product_name_snapshot,
                "unit_price": str(item.unit_price_snapshot),
                "quantity": item.quantity,
                "subtotal": str(item.subtotal),
                "options": [
                    {
                        "group": o.group_name_snapshot,
                        "name": o.option_name_snapshot,
                        "code": o.option_code,
                        "extra_price": str(o.extra_price_snapshot),
                    }
                    for o in opts
                ],
            }
        )
    store = await db.get(Store, order.store_id) if order.store_id else None
    from app.models import Customer
    from app.services.customer_display import is_customer_display_enabled, pickup_fields
    from app.services.customer_service import CustomerService
    table_obj = await db.get(DiningTable, order.table_id) if order.table_id else None
    has_display = await is_customer_display_enabled(
        db, int(order.store_id) if order.store_id else None
    )
    pickup = pickup_fields(order, table_obj, has_customer_display=has_display)
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
    from app.services.payment_service import PaymentService
    from app.services.social_benefit_service import SocialBenefitService

    social_snap = await SocialBenefitService.order_social_snapshot(db, int(order.id))
    # 详情需可退/已退字段，供后台订单页直接退款（列表页仍用轻量 tenders）
    payments = await PaymentService.list_paid_tenders_with_refund(db, int(order.id))
    return success_no_store(
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
            "payment_method": order.payment_method,
            "paid_at": serialize_dt(order.paid_at) if order.paid_at else None,
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
            "urge_count": order.urge_count or 0,
            "urged_at": serialize_dt(order.urged_at),
            "scheduled_pickup_at": serialize_dt(order.scheduled_pickup_at),
            "customer_id": order.customer_id,
            "customer_no": cust.customer_no if cust else None,
            "customer_name": (cust.display_name or cust.nickname) if cust else None,
            "customer_phone": cust.phone if cust else None,
            **member,
            "created_at": serialize_dt(order.created_at),
            "items": items,
            "payments": payments,
            "can_reorder": bool(items) and order.status not in ("PENDING_PAYMENT",),
            **pickup,
            **social_snap,
        },
        request=request,
    )

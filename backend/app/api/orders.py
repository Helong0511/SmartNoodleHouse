from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, or_, select

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, StaffOrCustomer, require_permission, require_pos
from app.core.exceptions import AppException
from app.core.redis_client import get_redis
from app.core.response import apply_date_range as _apply_date_range, calc_pages, ensure_local_aware, money, now_local, success, success_no_store, serialize_dt
from app.models import Customer, DeliveryOrder, DiningTable, Order, OrderItem, OrderItemOption, Product, Store
from app.schemas.order import OrderCreate, PosAddItems, PosBindCustomer, PosOrderCreate
from app.services.audit_service import AuditService
from app.services.customer_service import CustomerService, OrderCreateService
from app.services.store_scope import assert_order_store_access, assert_store_access, require_bound_store
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/orders", tags=["订单"])


from app.api.orders_common import load_stores as _load_stores
from app.api.orders_common import load_tables as _load_tables
from app.api.orders_common import table_payload as _table_payload
# 兼容测试与旧导入路径：厨房补打仍可从 orders 引用
from app.api.orders_kitchen import reprint_kitchen_ticket  # noqa: F401


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

    pos_customer_id = None
    if data.customer_id:
        from app.api.customers import _assert_customer_in_store

        cust = await db.get(Customer, int(data.customer_id))
        if not cust or int(cust.status) != 1:
            raise AppException(40406, "顾客不存在或已停用")
        await _assert_customer_in_store(db, int(data.customer_id), int(store_id), action="收银台绑客")
        pos_customer_id = int(data.customer_id)

    order = await OrderCreateService.create_order(
        db,
        store_id=store_id,
        table_token=table_token,
        customer_id=pos_customer_id,
        items=[i.model_dump() for i in data.items],
        remark=data.remark,
        channel="POS",
        idempotency_key=data.idempotency_key,
        need_pack=need_pack,
        dining_type=dtype,
        waive_packing_fee=bool(data.waive_packing_fee),
        operator_id=user.id,
        use_social_benefit=bool(data.use_social_benefit),
        social_rule_id=data.social_rule_id,
        customer_coupon_id=data.customer_coupon_id,
        customer_voucher_id=data.customer_voucher_id,
    )
    table = await db.get(DiningTable, order.table_id) if order.table_id else None
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
                "channel": "POS",
            },
        )
    except Exception as ws_err:
        # WS 推送失败不应导致下单事务回滚（顾客已视为下单成功）
        import logging

        logging.getLogger("app.order").warning("下单 WS 通知失败 order=%s: %s", order.order_no, ws_err)
    await AuditService.log_staff(
        db,
        user=user,
        action="order.create_pos",
        module="order",
        target_type="order",
        target_id=order.id,
        detail={
            "order_no": order.order_no,
            "store_id": int(order.store_id) if order.store_id else None,
            "dining_type": order.dining_type,
            "total_amount": str(order.total_amount),
            "table_id": int(order.table_id) if order.table_id else None,
            "customer_id": int(order.customer_id) if order.customer_id else None,
            "use_social_benefit": bool(data.use_social_benefit),
        },
        request=request,
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
            "customer_coupon_id": int(order.customer_coupon_id) if order.customer_coupon_id else None,
            "customer_voucher_id": int(order.customer_voucher_id) if order.customer_voucher_id else None,
            "packing_fee": str(order.packing_fee),
            "dining_type": order.dining_type,
            "status": order.status,
            "payment_status": order.payment_status,
            "customer_id": order.customer_id,
            "table_id": order.table_id,
            "table": _table_payload(table),
            **social_snap,
        },
        request=request,
    )


async def _recompute_pos_amounts_for_customer(
    db,
    order: Order,
    *,
    customer_coupon_id: int | None = None,
    customer_voucher_id: int | None = None,
    use_social_benefit: bool = False,
    social_rule_id: int | None = None,
    operator_id: int | None = None,
) -> None:
    """未收款 POS 单绑客/改券后重算：会员折 + 券，或公益（与券互斥；与会员折取更优）。"""
    from decimal import Decimal

    from app.services.coupon_service import CouponService
    from app.services.social_benefit_service import SocialBenefitService

    # 先释放旧券预占与公益台账，再按新选择重算
    await CouponService.release_for_order(db, order)
    order.customer_coupon_id = None
    order.customer_voucher_id = None
    order.coupon_discount = money(0)
    order.voucher_discount = money(0)
    await SocialBenefitService.reverse_for_order(db, int(order.id))

    items = list(
        (await db.execute(select(OrderItem).where(OrderItem.order_id == int(order.id)))).scalars().all()
    )
    discountable = money(0)
    for it in items:
        product = await db.get(Product, int(it.product_id))
        if product is None or not bool(getattr(product, "no_member_discount", False)):
            discountable = money(discountable + money(it.subtotal))

    subtotal = money(order.subtotal)
    packing_fee = money(order.packing_fee or 0)
    pack = (order.dining_type or "").upper() == "TAKEAWAY" or packing_fee > 0
    customer_id = int(order.customer_id) if order.customer_id else None

    if not customer_id:
        order.discount_amount = money(0)
        order.total_amount = money(subtotal + packing_fee)
        await db.flush()
        return

    if customer_coupon_id or customer_voucher_id:
        if use_social_benefit or social_rule_id:
            raise AppException(40008, "公益折扣不可与优惠券同享，请取消券后重试")

    social_hit = None
    if use_social_benefit or social_rule_id:
        pids = [int(it.product_id) for it in items]
        prods = (
            (await db.execute(select(Product).where(Product.id.in_(pids)))).scalars().all()
            if pids
            else []
        )
        cids = [int(p.category_id) for p in prods if p.category_id]
        if social_rule_id:
            from app.models import SocialBenefitRule

            rule = await db.get(SocialBenefitRule, int(social_rule_id))
            codes = await SocialBenefitService.active_codes(db, int(customer_id))
            if (
                rule
                and int(rule.status) == 1
                and rule.identity_code in codes
                and rule.benefit_type == "ORDER_DISCOUNT"
                and rule.discount_rate is not None
                and (rule.store_id is None or int(rule.store_id) == int(order.store_id))
            ):
                social_hit = (rule, rule.identity_code)
        if social_hit is None and use_social_benefit:
            # 显式勾选公益：必须命中，禁止静默改走会员折
            social_hit = await SocialBenefitService.require_best_rule(
                db,
                customer_id=int(customer_id),
                store_id=int(order.store_id),
                product_ids=pids,
                category_ids=cids,
            )

    apply_social = False
    social_subsidy = money(0)
    if social_hit:
        rule, identity_code = social_hit
        rate = money(rule.discount_rate)
        social_subsidy = money(discountable * (Decimal("1") - rate))
        if rule.max_subsidy_amount is not None:
            social_subsidy = min(social_subsidy, money(rule.max_subsidy_amount))
        member_rate = await CustomerService.resolve_member_discount_rate(db, customer_id)
        member_discount, _ = CustomerService.calc_member_discount(
            subtotal, packing_fee, member_rate, discountable_subtotal=discountable
        )
        apply_social = SocialBenefitService.social_beats_member(
            social_subsidy=social_subsidy,
            member_discount=member_discount,
        )
        if apply_social:
            special = money(subtotal - discountable)
            if special < 0:
                special = money(0)
            order.discount_amount = social_subsidy
            order.total_amount = money(discountable - social_subsidy + special + packing_fee)
            await SocialBenefitService.reserve_for_order(
                db,
                order_id=int(order.id),
                customer_id=int(customer_id),
                store_id=int(order.store_id),
                rule=rule,
                identity_code=identity_code,
                subsidy_amount=social_subsidy,
                operator_user_id=operator_id,
                meta={
                    "discount_rate": str(rate),
                    "stack_policy": rule.stack_policy,
                    "subtotal": str(subtotal),
                    "discountable_subtotal": str(discountable),
                    "source": "pos_recompute",
                    "picked_over_member": True,
                },
            )
            await db.flush()
            return

    rate = await CustomerService.resolve_member_discount_rate(db, customer_id)
    hold_tmpl = await CouponService.get_hold_template(db, customer_coupon_id, customer_id)
    effective_rate = CouponService.effective_member_rate_with_coupon(rate, hold_tmpl)
    discount_amount, base_total = CustomerService.calc_member_discount(
        subtotal, packing_fee, effective_rate, discountable_subtotal=discountable
    )
    coupon_eligible = CustomerService.calc_coupon_eligible_subtotal(
        subtotal, discountable, discount_amount
    )
    coupon_discount, coupon_id = await CouponService.resolve_discount(
        db,
        customer_coupon_id,
        customer_id,
        coupon_eligible,
        order_store_id=int(order.store_id) if getattr(order, "store_id", None) else None,
        member_rate=rate,
    )
    payable_before_voucher = money(base_total - coupon_discount)
    voucher_eligible = CustomerService.calc_voucher_eligible_payable(
        coupon_eligible, coupon_discount, packing_fee, need_pack=pack
    )
    voucher_discount, voucher_id = await CouponService.resolve_voucher_discount(
        db,
        customer_voucher_id,
        customer_id,
        payable_before_voucher,
        eligible_payable=voucher_eligible,
        order_store_id=int(order.store_id) if getattr(order, "store_id", None) else None,
    )
    order.discount_amount = discount_amount
    order.customer_coupon_id = coupon_id
    order.coupon_discount = coupon_discount
    order.customer_voucher_id = voucher_id
    order.voucher_discount = voucher_discount
    order.total_amount = money(payable_before_voucher - voucher_discount)
    if coupon_id:
        await CouponService.reserve_for_order(
            db, coupon_id, order_id=int(order.id), customer_id=customer_id
        )
    if voucher_id:
        await CouponService.reserve_for_order(
            db, voucher_id, order_id=int(order.id), customer_id=customer_id
        )
    await db.flush()


@router.post("/{order_id}/bind-customer", dependencies=[require_pos()])
async def bind_pos_customer(
    order_id: int,
    data: PosBindCustomer,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """待支付 POS 单绑定/更换/清除会员；重算会员折扣与优惠券。"""
    order = await db.get(Order, order_id)
    if not order:
        raise AppException(40404, "订单不存在")
    assert_order_store_access(user, order)
    if order.channel != "POS":
        raise AppException(40009, "仅收银台订单可绑客")
    if order.status != "PENDING_PAYMENT" or (order.payment_status or "").upper() != "UNPAID":
        raise AppException(40009, "仅未收款订单可绑定会员")

    new_cid = int(data.customer_id) if data.customer_id else None
    if new_cid:
        from app.api.customers import _assert_customer_in_store

        cust = await db.get(Customer, new_cid)
        if not cust or int(cust.status) != 1:
            raise AppException(40406, "顾客不存在或已停用")
        await _assert_customer_in_store(db, new_cid, int(order.store_id), action="收银台绑客")
    order.customer_id = new_cid
    await _recompute_pos_amounts_for_customer(
        db,
        order,
        customer_coupon_id=data.customer_coupon_id if new_cid else None,
        customer_voucher_id=data.customer_voucher_id if new_cid else None,
        use_social_benefit=bool(data.use_social_benefit) if new_cid else False,
        social_rule_id=data.social_rule_id if new_cid else None,
        operator_id=user.id,
    )
    # P2-15：审计日志须在 commit 之前写入，确保与订单变更在同一事务内
    await AuditService.log_staff(
        db,
        user=user,
        action="order.bind_customer",
        module="order",
        target_type="order",
        target_id=order.id,
        detail={
            "order_no": order.order_no,
            "customer_id": new_cid,
            "customer_coupon_id": int(order.customer_coupon_id) if order.customer_coupon_id else None,
            "customer_voucher_id": int(order.customer_voucher_id) if order.customer_voucher_id else None,
            "total_amount": str(order.total_amount),
            "discount_amount": str(order.discount_amount),
            "coupon_discount": str(order.coupon_discount or "0.00"),
            "voucher_discount": str(order.voucher_discount or "0.00"),
            "use_social_benefit": bool(data.use_social_benefit) if new_cid else False,
        },
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
    )
    await db.commit()
    cust = await db.get(Customer, new_cid) if new_cid else None
    from app.services.social_benefit_service import SocialBenefitService

    social_snap = await SocialBenefitService.order_social_snapshot(db, int(order.id))
    return success(
        {
            "id": order.id,
            "order_no": order.order_no,
            "customer_id": order.customer_id,
            "customer_name": (cust.display_name or cust.nickname) if cust else None,
            "customer_phone": cust.phone if cust else None,
            "total_amount": str(order.total_amount),
            "subtotal": str(order.subtotal),
            "discount_amount": str(order.discount_amount),
            "coupon_discount": str(order.coupon_discount or "0.00"),
            "voucher_discount": str(order.voucher_discount or "0.00"),
            "customer_coupon_id": int(order.customer_coupon_id) if order.customer_coupon_id else None,
            "customer_voucher_id": int(order.customer_voucher_id) if order.customer_voucher_id else None,
            "packing_fee": str(order.packing_fee),
            "payment_status": order.payment_status,
            "status": order.status,
            **social_snap,
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
    """收银台加菜：往已有待支付订单追加餐品，重算金额。"""
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
    from app.models import SocialBenefitRedemption
    from app.services.order_service import OrderPriceCalculator
    from app.services.social_benefit_service import SocialBenefitService

    add_subtotal, add_packing, _add_discountable = await OrderPriceCalculator.build_order_items(
        db, order, items_data, deduct_stock=True, need_pack=(order.dining_type == "TAKEAWAY"),
    )
    # 重算金额：原 subtotal + 加菜；优惠（会员折/券/公益）统一走 _recompute，避免加菜后丢券
    order.subtotal = money(order.subtotal) + add_subtotal
    order.packing_fee = money(order.packing_fee) + add_packing
    keep_coupon_id = int(order.customer_coupon_id) if order.customer_coupon_id else None
    keep_voucher_id = int(order.customer_voucher_id) if order.customer_voucher_id else None
    pending_social = (
        await db.execute(
            select(SocialBenefitRedemption.id).where(
                SocialBenefitRedemption.order_id == int(order.id),
                SocialBenefitRedemption.status == SocialBenefitService.REDEEM_PENDING,
            ).limit(1)
        )
    ).scalar_one_or_none()
    await _recompute_pos_amounts_for_customer(
        db,
        order,
        customer_coupon_id=keep_coupon_id,
        customer_voucher_id=keep_voucher_id,
        use_social_benefit=bool(pending_social),
        operator_id=user.id,
    )

    table = await db.get(DiningTable, order.table_id) if order.table_id else None
    await db.commit()
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
    await AuditService.log_staff(
        db,
        user=user,
        action="order.add_items",
        module="order",
        target_type="order",
        target_id=order.id,
        detail={
            "order_no": order.order_no,
            "store_id": int(order.store_id) if order.store_id else None,
            "subtotal": str(order.subtotal),
            "total_amount": str(order.total_amount),
            "coupon_discount": str(order.coupon_discount or "0.00"),
            "items_added": len(items_data),
        },
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
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
            "customer_coupon_id": int(order.customer_coupon_id) if order.customer_coupon_id else None,
            "customer_voucher_id": int(order.customer_voucher_id) if order.customer_voucher_id else None,
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

    await db.commit()
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
    await AuditService.log_staff(
        db,
        user=user,
        action="order.transfer_table",
        module="order",
        target_type="order",
        target_id=order.id,
        detail={
            "order_no": order.order_no,
            "store_id": int(order.store_id) if order.store_id else None,
            "old_table_id": old_table_id,
            "new_table_id": int(new_table.id),
        },
        request=request,
        store_id=int(order.store_id) if order.store_id else None,
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
    if sid is None:
        # 超管未指定门店时无法按单店列出挂单
        raise AppException(40008, "请选择门店")
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
    cust_ids = [int(o.customer_id) for o in orders_list if o.customer_id]
    cust_map: dict[int, Customer] = {}
    if cust_ids:
        custs = list((await db.execute(select(Customer).where(Customer.id.in_(cust_ids)))).scalars().all())
        cust_map = {int(c.id): c for c in custs}

    result_list = []
    for o in orders_list:
        o_items = items_by_order.get(o.id, [])
        tbl = table_map.get(o.table_id) if o.table_id else None
        cust = cust_map.get(int(o.customer_id)) if o.customer_id else None
        result_list.append({
            "id": o.id,
            "order_no": o.order_no,
            "table_id": o.table_id,
            "table_no": tbl.table_no if tbl else None,
            "table_name": tbl.table_name if tbl else None,
            "dining_type": o.dining_type,
            "total_amount": str(o.total_amount),
            "subtotal": str(o.subtotal),
            "discount_amount": str(o.discount_amount or 0),
            "coupon_discount": str(o.coupon_discount or "0.00"),
            "voucher_discount": str(o.voucher_discount or "0.00"),
            "customer_coupon_id": int(o.customer_coupon_id) if o.customer_coupon_id else None,
            "customer_voucher_id": int(o.customer_voucher_id) if o.customer_voucher_id else None,
            "customer_id": int(o.customer_id) if o.customer_id else None,
            "customer_name": (cust.display_name or cust.nickname) if cust else None,
            "customer_phone": cust.phone if cust else None,
            "item_count": len(o_items),
            "ordered_at": serialize_dt(o.ordered_at) if o.ordered_at else None,
            "remark": o.remark,
        })
    return success(result_list, request=request)


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

    from app.services.social_benefit_service import SocialBenefitService

    social_map = await SocialBenefitService.orders_social_map(
        db, [int(o.id) for o in order_rows]
    )

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
    table_map = await _load_tables(db, [o.table_id for o in rows if o.table_id])
    store_map = await _load_stores(db, [o.store_id for o in rows if o.store_id])
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
                "table": _table_payload(t),
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
    from app.services.social_benefit_service import SocialBenefitService

    social_snap = await SocialBenefitService.order_social_snapshot(db, int(order.id))
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
            "can_reorder": bool(items) and order.status not in ("PENDING_PAYMENT",),
            **pickup,
            **social_snap,
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




# ---------------------------------------------------------------------------
# 顾客自助服务：催单 / 支付成功领券 / 未制作整单退款申请
# ---------------------------------------------------------------------------

_URGE_ALLOWED_STATUSES = {"CONFIRMED", "PREPARING", "COOKING"}


async def _get_customer_order_or_404(db, order_id: int, customer_id: int) -> Order:
    order = await db.get(Order, int(order_id))
    if not order or order.customer_id != int(customer_id):
        # 他人订单不暴露存在性
        raise AppException(40404, "订单不存在")
    return order


@router.post("/{order_id}/urge")
async def urge_order(order_id: int, request: Request, db: DbSession, customer: CurrentCustomer):
    """顾客自助催单：限 CONFIRMED/PREPARING/COOKING；5 分钟限频（Redis 故障 fail-open）。"""
    order = await _get_customer_order_or_404(db, order_id, customer.id)
    if order.status not in _URGE_ALLOWED_STATUSES:
        raise AppException(40058, "当前订单状态无法催单")
    limit_key = f"snh:order:urge:{int(order.id)}"
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
    from app.models import CouponTemplate, CustomerCoupon

    order = await _get_customer_order_or_404(db, order_id, customer.id)
    if order.payment_status != "PAID":
        return success({"available": False, "coupon_name": None, "claimed": False}, request=request)
    from app.services.system_settings_service import SystemSettingsService

    name = (await SystemSettingsService.get_value(db, "pay_success_coupon_name", "") or "").strip()
    if not name:
        return success({"available": False, "coupon_name": None, "claimed": False}, request=request)
    tmpl = (
        await db.execute(
            select(CouponTemplate).where(
                CouponTemplate.name == name,
                CouponTemplate.status == 1,
            ).order_by(CouponTemplate.id.desc()).limit(1)
        )
    ).scalars().first()
    if not tmpl:
        return success({"available": False, "coupon_name": name, "claimed": False}, request=request)
    claimed = (
        await db.execute(
            select(CustomerCoupon.id).where(
                CustomerCoupon.customer_id == customer.id,
                CustomerCoupon.template_id == tmpl.id,
                CustomerCoupon.client_request_id == f"WELCOME:{int(customer.id)}:{int(tmpl.id)}",
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
    """支付成功后领取活动券（幂等：issue_from_template 内部按顾客+模板去重）。"""
    from app.services.coupon_service import CouponService

    order = await _get_customer_order_or_404(db, order_id, customer.id)
    if order.payment_status != "PAID":
        raise AppException(40059, "订单支付完成后方可领券")
    from app.services.system_settings_service import SystemSettingsService

    name = (await SystemSettingsService.get_value(db, "pay_success_coupon_name", "") or "").strip()
    if not name:
        raise AppException(40060, "领券活动未开启")
    issued = await CouponService.issue_from_template(
        db, int(customer.id), name, store_id=int(order.store_id) if order.store_id else None
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

    order = await _get_customer_order_or_404(db, order_id, customer.id)
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

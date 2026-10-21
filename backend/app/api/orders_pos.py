"""收银台 POS 下单/绑客/加菜/转台/挂单：从 orders 路由拆出，路径仍挂在 /api/orders。"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_pos
from app.api.orders_common import load_tables, table_payload
from app.core.exceptions import AppException
from app.core.redis_client import get_redis
from app.core.response import money, serialize_dt, success
from app.models import Customer, DiningTable, Order, OrderItem, Product
from app.schemas.order import PosAddItems, PosBindCustomer, PosOrderCreate
from app.services.audit_service import AuditService
from app.services.customer_service import CustomerService, OrderCreateService
from app.services.store_scope import assert_order_store_access, assert_store_access, require_bound_store
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/orders", tags=["订单"])


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
            "table": table_payload(table),
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
            **CustomerService.staff_customer_name_fields(cust),
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
                    "table": table_payload(table),
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
            "table": table_payload(table),
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
    table_map = await load_tables(db, table_ids) if table_ids else {}
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
            **CustomerService.staff_customer_name_fields(cust),
            "customer_phone": cust.phone if cust else None,
            "item_count": len(o_items),
            "ordered_at": serialize_dt(o.ordered_at) if o.ordered_at else None,
            "remark": o.remark,
        })
    return success(result_list, request=request)



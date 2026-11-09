"""堂食与外带下单：幂等创建订单，并推进厨房状态。"""
import hashlib
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.biz_no import BIZ_OT, generate_no, resolve_table_parts
from app.core.exceptions import AppException
from app.core.response import money, now_local
from app.models import Option, OptionGroup, Order, OrderItem, OrderItemOption, Store
from app.services.customer_service import CustomerService
from app.services.order_service import OrderPriceCalculator


class OrderCreateService:
    @staticmethod
    def _should_apply_social_benefit(customer_id: int | None, use_social_benefit: bool) -> bool:
        """公益折扣必须由顾客显式勾选；旧版 social_rule_id 仅兼容接收，不参与资格判断。"""
        return customer_id is not None and bool(use_social_benefit)

    @staticmethod
    def _is_reusable_idempotent_order(order: Order) -> bool:
        """仅待支付未结清订单可被幂等键复用。"""
        return (order.status or "") == "PENDING_PAYMENT" and (order.payment_status or "") in (
            "UNPAID",
            "PENDING",
        )

    @staticmethod
    def _scoped_idempotency_key(
        raw: str | None,
        *,
        channel: str | None,
        customer_id: int | None,
        store_id: int | None,
        operator_id: int | None,
    ) -> str | None:
        """幂等键绑定业务主体，避免全局撞 key。"""
        key = (raw or "").strip()
        if not key:
            return None
        ch = (channel or "").strip().upper()
        if ch == "POS":
            if not store_id or not operator_id:
                raise AppException(40008, "POS 下单幂等键须绑定门店与操作员")
            scope = f"pos:{int(store_id)}:{int(operator_id)}:"
        elif customer_id:
            scope = f"cust:{int(customer_id)}:"
        elif store_id:
            scope = f"store:{int(store_id)}:"
        else:
            scope = "request:"
        combined = f"{scope}{key}"
        if len(combined) <= 64:
            return combined
        # 不可直接截断：相同长前缀会碰撞并错误复用他人/另一购物车的订单。
        digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()
        # 超长键直接返回完整 256-bit 摘要；scope 已参与哈希，不再截断摘要位数。
        return digest

    @staticmethod
    def _assert_idempotent_order_match(
        order: Order,
        *,
        customer_id: int | None,
        store_id: int | None,
        channel: str | None,
        operator_id: int | None,
        dining_type: str | None = None,
        table_id: int | None = None,
        remark: str | None = None,
        customer_coupon_id: int | None = None,
        customer_voucher_id: int | None = None,
        scheduled_pickup_at: str | None = None,
    ) -> None:
        """命中幂等订单后必须再次校验归属，禁止直接 return 他人订单。"""
        ch = (channel or "").strip().upper()
        if ch and (order.channel or "").strip().upper() != ch:
            raise AppException(40902, "幂等键冲突：渠道不一致")
        if store_id is not None and int(order.store_id) != int(store_id):
            raise AppException(40902, "幂等键冲突：门店不一致")
        if customer_id is not None:
            if order.customer_id is None or int(order.customer_id) != int(customer_id):
                raise AppException(40902, "幂等键冲突：顾客不一致")
        if ch == "POS" and operator_id is not None:
            oid = getattr(order, "operator_id", None)
            if oid is not None and int(oid) != int(operator_id):
                raise AppException(40902, "幂等键冲突：操作员不一致")
        # P0-15: 校验订单核心要素，防止同 key 不同参数
        dtype = (dining_type or "").strip().upper()
        if dtype:
            order_dtype = (getattr(order, "dining_type", "") or "").strip().upper()
            if order_dtype and order_dtype != dtype:
                raise AppException(40902, "幂等键冲突：就餐方式不一致")
        if table_id is not None:
            order_tid = getattr(order, "table_id", None)
            if order_tid is not None and int(order_tid) != int(table_id):
                raise AppException(40902, "幂等键冲突：桌台不一致")
        if (getattr(order, "remark", None) or "") != (remark or ""):
            raise AppException(40902, "幂等键冲突：订单备注不一致")
        if getattr(order, "customer_coupon_id", None) != customer_coupon_id:
            raise AppException(40902, "幂等键冲突：优惠券不一致")
        if getattr(order, "customer_voucher_id", None) != customer_voucher_id:
            raise AppException(40902, "幂等键冲突：代金券不一致")
        requested_pickup = None
        if scheduled_pickup_at:
            try:
                requested_pickup = datetime.fromisoformat(scheduled_pickup_at)
            except ValueError as exc:
                raise AppException(40061, "预约取餐时间格式不正确") from exc
            if requested_pickup.tzinfo is not None:
                requested_pickup = requested_pickup.replace(tzinfo=None)
        existing_pickup = getattr(order, "scheduled_pickup_at", None)
        if existing_pickup is not None and existing_pickup.tzinfo is not None:
            existing_pickup = existing_pickup.replace(tzinfo=None)
        if existing_pickup != requested_pickup:
            raise AppException(40902, "幂等键冲突：预约取餐时间不一致")

    @staticmethod
    async def _assert_idempotent_order_items_match(
        db: AsyncSession, order: Order, items: list[dict]
    ) -> None:
        """幂等重放必须匹配原订单的餐品、数量、备注和规格，防止同 key 偷换购物车。"""
        existing_items = (
            await db.execute(
                select(OrderItem)
                .where(OrderItem.order_id == int(order.id))
                .order_by(OrderItem.id)
            )
        ).scalars().all()
        if len(existing_items) != len(items):
            raise AppException(40902, "幂等键冲突：订单餐品数量不一致")

        requested_option_ids = [
            int(option_id)
            for item in items
            for option_id in (item.get("option_ids") or [])
        ]
        options_by_id = {}
        if requested_option_ids:
            options = (
                await db.execute(select(Option).where(Option.id.in_(set(requested_option_ids))))
            ).scalars().all()
            options_by_id = {int(option.id): option for option in options}

        item_ids = [int(item.id) for item in existing_items]
        existing_options = (
            await db.execute(
                select(OrderItemOption).where(OrderItemOption.order_item_id.in_(item_ids))
            )
        ).scalars().all() if item_ids else []
        option_groups_by_id = {}
        requested_group_ids = {
            int(option.group_id)
            for option in options_by_id.values()
            if getattr(option, "group_id", None) is not None
        }
        if requested_group_ids:
            groups = (
                await db.execute(select(OptionGroup).where(OptionGroup.id.in_(requested_group_ids)))
            ).scalars().all()
            option_groups_by_id = {int(group.id): group for group in groups}
        option_codes_by_item: dict[int, list[tuple[str, str, int]]] = {}
        for option in existing_options:
            option_codes_by_item.setdefault(int(option.order_item_id), []).append(
                (
                    str(option.group_name_snapshot or ""),
                    str(option.option_code or ""),
                    int(option.quantity or 1),
                )
            )

        existing_signatures = []
        for existing in existing_items:
            existing_signatures.append((
                int(existing.product_id),
                int(existing.quantity),
                existing.remark or "",
                tuple(sorted(option_codes_by_item.get(int(existing.id), []))),
            ))

        requested_signatures = []
        for requested in items:
            requested_codes = []
            for option_id in (requested.get("option_ids") or []):
                option = options_by_id.get(int(option_id))
                group = option_groups_by_id.get(int(option.group_id)) if option else None
                if option is None or group is None:
                    raise AppException(40902, "幂等键冲突：规格选项不存在")
                requested_codes.append((str(group.name or ""), str(option.code or ""), 1))
            requested_signatures.append((
                int(requested.get("product_id") or 0),
                int(requested.get("quantity") or 0),
                requested.get("remark") or "",
                tuple(sorted(requested_codes)),
            ))

        # 按规范化签名比较，不依赖用户在购物车中调整餐品行顺序。
        if sorted(existing_signatures) != sorted(requested_signatures):
            raise AppException(40902, "幂等键冲突：餐品、数量、备注或规格不一致")

    @staticmethod
    async def create_order(
        db: AsyncSession,
        *,
        store_id: int | None,
        table_token: str | None,
        customer_id: int | None,
        items: list[dict],
        remark: str | None = None,
        channel: str | None = None,
        idempotency_key: str | None = None,
        need_pack: bool = False,
        dining_type: str | None = None,
        customer_coupon_id: int | None = None,
        customer_voucher_id: int | None = None,
        waive_packing_fee: bool = False,
        operator_id: int | None = None,
        skip_promotions: bool = False,
        scheduled_pickup_at: str | None = None,
        use_social_benefit: bool = False,
        social_rule_id: int | None = None,
    ) -> Order:
        from sqlalchemy.exc import IntegrityError

        from app.services.status_history import record_order_status

        table_id = None
        table_no_raw: str | None = None
        table_token = (table_token or "").strip() or None
        if table_token:
            from app.services.table_resolve import resolve_dining_table

            # 有 table_token 时以桌台解析出门店为准，避免前端缓存 store_id 与桌台不一致
            table = await resolve_dining_table(db, table_token, store_id=store_id)
            if not table:
                raise AppException(40403, "无效桌台二维码（请扫桌码，或确认页选择桌号）")
            table_id = table.id
            store_id = table.store_id
            table_no_raw = table.table_no
            # 开发用桌号作 token 时，后续支付/展示统一落到真实 qr_token 绑定的桌
        else:
            # 堂食（微信/支付宝）必须绑桌台，否则厨房/订单无法显示桌号
            ch = (channel or "").upper()
            if ch in ("WECHAT", "ALIPAY"):
                raise AppException(40008, "堂食请先扫桌台二维码，或在确认页选择桌号")
            if not store_id or int(store_id) <= 0:
                raise AppException(40008, "缺少桌台令牌或有效门店")
            # H5/外带无桌台令牌时，门店 ID 来自请求参数，必须重新验证存在且启用。
            store = await db.get(Store, int(store_id))
            if not store:
                raise AppException(40401, "门店不存在")
            if int(store.status or 0) != 1:
                raise AppException(40302, "该门店当前不可下单")

        scoped_key = OrderCreateService._scoped_idempotency_key(
            idempotency_key,
            channel=channel,
            customer_id=customer_id,
            store_id=store_id,
            operator_id=operator_id,
        )
        if scoped_key:
            existing = await db.execute(select(Order).where(Order.idempotency_key == scoped_key))
            order = existing.scalar_one_or_none()
            if order:
                OrderCreateService._assert_idempotent_order_match(
                    order,
                    customer_id=customer_id,
                    store_id=store_id,
                    channel=channel,
                    operator_id=operator_id,
                    dining_type=dining_type or ("TAKEAWAY" if need_pack else "DINE_IN"),
                    table_id=table_id,
                    remark=remark,
                    customer_coupon_id=customer_coupon_id,
                    customer_voucher_id=customer_voucher_id,
                    scheduled_pickup_at=scheduled_pickup_at,
                )
                # 仅复用待支付单；已支付/取消等同内容再下须换新幂等键（客户端带 checkout nonce）
                if not OrderCreateService._is_reusable_idempotent_order(order):
                    raise AppException(40903, "该幂等键对应订单已结束，请重新下单")
                await OrderCreateService._assert_idempotent_order_items_match(db, order, items)
                return order

        dtype = (dining_type or "").strip().upper()
        if dtype not in ("DINE_IN", "TAKEAWAY"):
            dtype = "TAKEAWAY" if need_pack else "DINE_IN"
        pack = need_pack or dtype == "TAKEAWAY"
        charge_pack = pack and not waive_packing_fee
        # 预点单：仅打包单支持预约取餐时段（未来 10 分钟 ~ 2 天内）
        scheduled_dt: datetime | None = None
        raw_scheduled = (scheduled_pickup_at or "").strip()
        if raw_scheduled and dtype == "TAKEAWAY":
            try:
                scheduled_dt = datetime.fromisoformat(raw_scheduled)
            except ValueError:
                raise AppException(40061, "预约取餐时间格式不正确")
            if scheduled_dt.tzinfo is not None:
                scheduled_dt = scheduled_dt.replace(tzinfo=None)
            now = now_local()
            if now.tzinfo is not None:
                now = now.replace(tzinfo=None)
            if scheduled_dt < now + timedelta(minutes=10):
                raise AppException(40062, "预约取餐时间至少需晚于当前 10 分钟")
            if scheduled_dt > now + timedelta(days=2):
                raise AppException(40063, "预约取餐时间不能超过 2 天后")
        t_no, t_ph = resolve_table_parts(table_no_raw, channel=channel, dining_type=dtype)
        store = await db.get(Store, int(store_id)) if store_id else None
        if not store or int(getattr(store, "status", 0) or 0) != 1:
            raise AppException(40011, "门店不存在或已停业")
        sno = store.store_no if store else None

        order_kwargs = dict(
            order_no=generate_no(BIZ_OT, sno, t_no, table_placeholder=t_ph),
            customer_id=customer_id,
            table_id=table_id,
            store_id=store_id,
            status="PENDING_PAYMENT",
            payment_status="UNPAID",
            channel=channel,
            dining_type=dtype,
            remark=remark,
            idempotency_key=scoped_key,
            ordered_at=now_local(),
        )
        if scheduled_dt is not None:
            order_kwargs["scheduled_pickup_at"] = scheduled_dt
        # 关联当前 ACTIVE 就餐会话（过期则不挂旧桌）
        if customer_id and table_id and hasattr(Order, "dining_session_id"):
            from app.services.dining_session_service import DiningSessionService

            active = await DiningSessionService.get_active(db, int(customer_id))
            if active and int(active.table_id) == int(table_id):
                order_kwargs["dining_session_id"] = active.id
                await DiningSessionService.touch(db, active.id)
            elif table_token:
                from app.services.table_resolve import resolve_dining_table

                tbl = await resolve_dining_table(db, table_token, store_id=store_id)
                if tbl:
                    sess = await DiningSessionService.start_or_refresh(
                        db,
                        customer_id=int(customer_id),
                        table=tbl,
                        table_token=table_token,
                    )
                    order_kwargs["dining_session_id"] = sess.id
        # operator_id 列在迁移后存在；旧库无该字段时忽略
        if hasattr(Order, "operator_id") and operator_id is not None:
            order_kwargs["operator_id"] = operator_id
        order = Order(**order_kwargs)
        db.add(order)
        try:
            await db.flush()
        except IntegrityError:
            # 并发幂等：唯一约束冲突后返回已有订单
            await db.rollback()
            if scoped_key:
                existing = await db.execute(select(Order).where(Order.idempotency_key == scoped_key))
                order = existing.scalar_one_or_none()
                if order:
                    OrderCreateService._assert_idempotent_order_match(
                        order,
                        customer_id=customer_id,
                        store_id=store_id,
                        channel=channel,
                        operator_id=operator_id,
                        dining_type=dining_type or ("TAKEAWAY" if need_pack else "DINE_IN"),
                        table_id=table_id,
                        remark=remark,
                        customer_coupon_id=customer_coupon_id,
                        customer_voucher_id=customer_voucher_id,
                        scheduled_pickup_at=scheduled_pickup_at,
                    )
                    if not OrderCreateService._is_reusable_idempotent_order(order):
                        raise AppException(40903, "该幂等键对应订单已结束，请重新下单")
                    await OrderCreateService._assert_idempotent_order_items_match(db, order, items)
                    return order
            raise AppException(40901, "订单创建冲突，请重试")

        subtotal, packing_fee, discountable_subtotal = await OrderPriceCalculator.build_order_items(
            db, order, items, deduct_stock=True, need_pack=charge_pack
        )
        social_applied = False
        if skip_promotions:
            # 余额支付：按原价（含餐盒），不叠会员折扣/券/代金券/公益
            discount_amount = money(0)
            coupon_discount = money(0)
            voucher_discount = money(0)
            coupon_id = None
            voucher_id = None
            total_amount = money(subtotal + packing_fee)
        else:
            from app.services.coupon_service import CouponService
            from app.services.social_benefit_service import SocialBenefitService
            from app.models import Product

            # 公益与券互斥；与会员折比补贴，取更优（折扣更低者）
            social_hit = None
            if OrderCreateService._should_apply_social_benefit(customer_id, use_social_benefit):
                if customer_coupon_id or customer_voucher_id:
                    raise AppException(40008, "公益折扣不可与优惠券同享，请取消券后重试")
                pids = [int(i["product_id"]) for i in items]
                from app.models import Product as _Product

                prods = (
                    await db.execute(select(_Product).where(_Product.id.in_(pids)))
                ).scalars().all() if pids else []
                cids = [int(p.category_id) for p in prods if p.category_id]
                if use_social_benefit:
                    # social_rule_id 不作为规则选择依据；
                    # 服务端统一校验日期/星期/时段/限次/餐品/分类/折扣率。
                    social_hit = await SocialBenefitService.require_best_rule(
                        db,
                        customer_id=int(customer_id),
                        store_id=int(order.store_id),
                        product_ids=pids,
                        category_ids=cids,
                    )

            apply_social = False
            social_subsidy = money(0)
            social_rule = None
            social_identity_code = None
            if social_hit:
                social_rule, social_identity_code = social_hit
                rate = money(social_rule.discount_rate)
                social_subsidy = money(discountable_subtotal * (Decimal("1") - rate))
                if social_rule.max_subsidy_amount is not None:
                    social_subsidy = min(social_subsidy, money(social_rule.max_subsidy_amount))
                member_rate = await CustomerService.resolve_member_discount_rate(
                    db, customer_id
                )
                member_discount, _ = CustomerService.calc_member_discount(
                    subtotal,
                    packing_fee,
                    member_rate,
                    discountable_subtotal=discountable_subtotal,
                )
                apply_social = SocialBenefitService.social_beats_member(
                    social_subsidy=social_subsidy,
                    member_discount=member_discount,
                )

            if apply_social and social_rule is not None and social_identity_code is not None:
                special = money(subtotal - discountable_subtotal)
                if special < 0:
                    special = money(0)
                discount_amount = social_subsidy
                coupon_discount = money(0)
                voucher_discount = money(0)
                coupon_id = None
                voucher_id = None
                total_amount = money(
                    discountable_subtotal - social_subsidy + special + packing_fee
                )
                await SocialBenefitService.reserve_for_order(
                    db,
                    order_id=int(order.id),
                    customer_id=int(customer_id),
                    store_id=int(order.store_id),
                    rule=social_rule,
                    identity_code=social_identity_code,
                    subsidy_amount=social_subsidy,
                    operator_user_id=operator_id,
                    meta={
                        "discount_rate": str(money(social_rule.discount_rate)),
                        "stack_policy": social_rule.stack_policy,
                        "subtotal": str(subtotal),
                        "discountable_subtotal": str(discountable_subtotal),
                        "picked_over_member": True,
                    },
                )
                social_applied = True
            else:
                rate = await CustomerService.resolve_member_discount_rate(db, customer_id)
                # PERCENT 与会员折互斥：券力度严格更优时跳过会员折，由折扣券独占（取最优）
                hold_tmpl = await CouponService.get_hold_template(db, customer_coupon_id, customer_id)
                effective_rate = CouponService.effective_member_rate_with_coupon(rate, hold_tmpl)
                discount_amount, base_total = CustomerService.calc_member_discount(
                    subtotal, packing_fee, effective_rate, discountable_subtotal=discountable_subtotal
                )
                coupon_eligible = CustomerService.calc_coupon_eligible_subtotal(
                    subtotal, discountable_subtotal, discount_amount
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
                total_amount = money(payable_before_voucher - voucher_discount)
        order.subtotal = subtotal
        order.discount_amount = discount_amount
        order.customer_coupon_id = coupon_id
        order.coupon_discount = coupon_discount
        order.customer_voucher_id = voucher_id
        order.voucher_discount = voucher_discount
        order.packing_fee = packing_fee
        order.total_amount = total_amount
        if coupon_id:
            from app.services.coupon_service import CouponService

            await CouponService.reserve_for_order(
                db, coupon_id, order_id=order.id, customer_id=customer_id
            )
        if voucher_id:
            from app.services.coupon_service import CouponService

            await CouponService.reserve_for_order(
                db, voucher_id, order_id=order.id, customer_id=customer_id
            )
        _ = social_applied  # 台账已在 reserve_for_order 写入 PENDING
        await record_order_status(
            db, order_id=order.id, from_status=None, to_status="PENDING_PAYMENT", source="SYSTEM"
        )
        await db.flush()
        return order

    @staticmethod
    async def update_status(
        db: AsyncSession,
        order_id: int,
        status: str,
        *,
        operator_id: int | None = None,
        force_takeover: bool = False,
    ) -> Order:
        from app.services.kitchen_claim import apply_kitchen_claim_for_status
        from app.services.order_lifecycle import apply_order_status

        order = await db.get(Order, order_id)
        if not order:
            raise AppException(40404, "订单不存在")
        await apply_kitchen_claim_for_status(
            db,
            order,
            status,
            operator_id=operator_id,
            force_takeover=force_takeover,
        )
        order = await apply_order_status(
            db,
            order,
            status,
            source="STAFF",
            operator_id=operator_id,
            assert_kind="STAFF",
        )
        return order

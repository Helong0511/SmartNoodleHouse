from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.biz_no import BIZ_CU, BIZ_OT, generate_no, resolve_table_parts
from app.core.exceptions import AppException
from app.core.response import money, now_local
from app.models import (
    Customer,
    CustomerAlipayAccount,
    CustomerPreference,
    CustomerWechatAccount,
    DiningTable,
    MemberLevel,
    Order,
    OrderItem,
    OrderItemOption,
    Store,
)
from app.services.order_service import OrderPriceCalculator

DEFAULT_MEMBER_LEVEL_NAME = "普通会员"


class CustomerService:
    @staticmethod
    def keyword_condition(keyword: str | None):
        """昵称 / 显示名 / 手机 / 顾客编号 模糊匹配。"""
        kw = (keyword or "").strip()
        if not kw:
            return None
        like = f"%{kw}%"
        return or_(
            Customer.display_name.like(like),
            Customer.nickname.like(like),
            Customer.phone.like(like),
            Customer.customer_no.like(like),
        )

    @staticmethod
    async def search_active_customers(
        db: AsyncSession,
        keyword: str,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Customer], int]:
        """全库搜索启用顾客（不受门店下单范围限制，供发券等场景）。"""
        kw = (keyword or "").strip()
        if not kw:
            return [], 0
        cond = CustomerService.keyword_condition(kw)
        q = select(Customer).where(Customer.status == 1).where(cond)
        total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
        page = max(1, page)
        page_size = min(max(1, page_size), 50)
        rows = list(
            (
                await db.execute(
                    q.order_by(Customer.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
                )
            ).scalars().all()
        )
        return rows, int(total)

    @staticmethod
    def member_payload(customer: Customer, level: MemberLevel | None = None) -> dict:
        """顾客会员关联摘要（列表/详情复用）。"""
        registered = bool(
            customer.member_level_id
            or (customer.display_name and str(customer.display_name).strip())
            or (customer.nickname and str(customer.nickname).strip())
            or (customer.phone and str(customer.phone).strip())
        )
        return {
            "is_member": registered,
            "member_level_id": customer.member_level_id,
            "member_level_name": level.name if level else None,
            "member_discount_rate": str(level.discount_rate) if level else None,
            "member_min_points": int(level.min_points) if level else None,
            "member_points": int(customer.points or 0),
        }

    @staticmethod
    def discount_display_text(rate: Decimal | None) -> str | None:
        if rate is None:
            return None
        r = money(rate)
        if r >= Decimal("1"):
            return None
        zhe = (r * Decimal("10")).quantize(Decimal("0.1"))
        return f"{zhe}折"

    @staticmethod
    async def list_levels_ordered(db: AsyncSession) -> list[MemberLevel]:
        return list(
            (await db.execute(select(MemberLevel).order_by(MemberLevel.min_points.asc()))).scalars().all()
        )

    @staticmethod
    async def recalculate_member_level(db: AsyncSession, customer: Customer) -> MemberLevel | None:
        """按积分重算并写入会员等级（取可达最高档）。"""
        levels = await CustomerService.list_levels_ordered(db)
        if not levels:
            return None
        pts = int(customer.points or 0)
        chosen = levels[0]
        for lv in levels:
            if pts >= int(lv.min_points):
                chosen = lv
        if customer.member_level_id != chosen.id:
            customer.member_level_id = chosen.id
            await db.flush()
        return chosen

    @staticmethod
    async def award_points_for_paid_order(db: AsyncSession, order: Order) -> int:
        """支付成功：实付 1 元 = 1 积分，并尝试升级等级。"""
        if not order.customer_id:
            return 0
        customer = await db.get(Customer, int(order.customer_id))
        if not customer:
            return 0
        earned = int(money(order.total_amount or 0))
        if earned <= 0:
            return 0
        customer.points = int(customer.points or 0) + earned
        await CustomerService.recalculate_member_level(db, customer)
        await db.flush()
        return earned

    @staticmethod
    def member_progress_payload(
        customer: Customer, level: MemberLevel | None, all_levels: list[MemberLevel]
    ) -> dict:
        points = int(customer.points or 0)
        current_min = int(level.min_points) if level else 0
        next_level: MemberLevel | None = None
        for lv in all_levels:
            if int(lv.min_points) > points:
                next_level = lv
                break
        points_to_next = max(0, int(next_level.min_points) - points) if next_level else 0
        progress_pct = 100
        if next_level and level:
            span = int(next_level.min_points) - current_min
            if span > 0:
                progress_pct = min(100, max(0, int((points - current_min) / span * 100)))
        levels_public = []
        for lv in all_levels:
            rate = money(lv.discount_rate)
            levels_public.append(
                {
                    "id": lv.id,
                    "name": lv.name,
                    "min_points": int(lv.min_points),
                    "discount_rate": str(rate),
                    "discount_text": CustomerService.discount_display_text(rate),
                    "is_current": bool(level and lv.id == level.id),
                    "unlocked": points >= int(lv.min_points),
                }
            )
        return {
            "next_level_name": next_level.name if next_level else None,
            "next_level_min_points": int(next_level.min_points) if next_level else None,
            "points_to_next_level": points_to_next,
            "level_progress_percent": progress_pct,
            "member_levels": levels_public,
            "points_rule": "实付1元=1积分，积分达标自动升级享对应折扣",
        }

    @staticmethod
    async def build_member_profile(db: AsyncSession, customer: Customer) -> dict:
        level = None
        if customer.member_level_id:
            level = await db.get(MemberLevel, customer.member_level_id)
        all_levels = await CustomerService.list_levels_ordered(db)
        if not level and all_levels:
            level = all_levels[0]
        return {
            **CustomerService.member_payload(customer, level),
            **CustomerService.member_progress_payload(customer, level, all_levels),
        }

    @staticmethod
    async def load_levels_map(db: AsyncSession, level_ids: list[int | None]) -> dict[int, MemberLevel]:
        ids = list({int(i) for i in level_ids if i})
        if not ids:
            return {}
        rows = (await db.execute(select(MemberLevel).where(MemberLevel.id.in_(ids)))).scalars().all()
        return {m.id: m for m in rows}

    @staticmethod
    async def ensure_default_member_level(db: AsyncSession, customer: Customer) -> MemberLevel | None:
        """注册会员时若无等级则挂「普通会员」。"""
        if customer.member_level_id:
            return await db.get(MemberLevel, customer.member_level_id)
        level = (
            await db.execute(select(MemberLevel).where(MemberLevel.name == DEFAULT_MEMBER_LEVEL_NAME).limit(1))
        ).scalar_one_or_none()
        if not level:
            level = (
                await db.execute(select(MemberLevel).order_by(MemberLevel.min_points.asc()).limit(1))
            ).scalar_one_or_none()
        if level:
            customer.member_level_id = level.id
            await db.flush()
        return level

    @staticmethod
    async def resolve_member_discount_rate(db: AsyncSession, customer_id: int | None) -> Decimal:
        """会员等级折扣率（0~1）；无等级或无效时返回 1（不打折）。"""
        if not customer_id:
            return Decimal("1")
        customer = await db.get(Customer, int(customer_id))
        if not customer or not customer.member_level_id:
            return Decimal("1")
        level = await db.get(MemberLevel, customer.member_level_id)
        if not level or level.discount_rate is None:
            return Decimal("1")
        rate = money(level.discount_rate)
        if rate <= Decimal("0") or rate > Decimal("1"):
            return Decimal("1")
        return rate

    @staticmethod
    def calc_member_discount(
        subtotal: Decimal,
        packing_fee: Decimal,
        rate: Decimal,
        *,
        discountable_subtotal: Decimal | None = None,
    ) -> tuple[Decimal, Decimal]:
        """按可打折商品小计计算会员优惠（打包费、特价商品不参与折扣）。返回 (discount_amount, total_amount)。"""
        subtotal = money(subtotal)
        discountable_subtotal = money(discountable_subtotal if discountable_subtotal is not None else subtotal)
        if discountable_subtotal > subtotal:
            discountable_subtotal = subtotal
        packing_fee = money(packing_fee)
        rate = money(rate)
        if rate >= Decimal("1"):
            return Decimal("0.00"), money(subtotal + packing_fee)
        discount = money(discountable_subtotal * (Decimal("1") - rate))
        total = money(subtotal - discount + packing_fee)
        return discount, total

    @staticmethod
    def calc_coupon_eligible_subtotal(
        subtotal: Decimal,
        discountable_subtotal: Decimal,
        discount_amount: Decimal,
    ) -> Decimal:
        """会员折后可用于优惠券的商品小计（不含特价商品）。"""
        subtotal = money(subtotal)
        discountable_subtotal = money(discountable_subtotal)
        if discountable_subtotal > subtotal:
            discountable_subtotal = subtotal
        special_subtotal = money(subtotal - discountable_subtotal)
        after_member = money(subtotal - money(discount_amount))
        eligible = money(after_member - special_subtotal)
        return eligible if eligible > Decimal("0") else Decimal("0.00")

    @staticmethod
    def calc_voucher_eligible_payable(
        coupon_eligible_subtotal: Decimal,
        coupon_discount: Decimal,
        packing_fee: Decimal,
        *,
        need_pack: bool,
    ) -> Decimal:
        """可用于代金券门槛/面额判断的基数（不含特价商品小计，可选含打包费）。"""
        base = money(coupon_eligible_subtotal) - money(coupon_discount)
        if base < Decimal("0"):
            base = Decimal("0.00")
        if need_pack:
            base = money(base + money(packing_fee))
        return base

    @staticmethod
    async def get_or_create_by_wechat(db: AsyncSession, openid: str, unionid: str | None = None) -> Customer:
        result = await db.execute(select(CustomerWechatAccount).where(CustomerWechatAccount.openid == openid))
        account = result.scalar_one_or_none()
        if account:
            if unionid and not account.unionid:
                account.unionid = unionid
                await db.flush()
            return await db.get(Customer, account.customer_id)

        # 同一开放平台主体下，unionid 可跨小程序识别同一人
        if unionid:
            by_union = await db.execute(
                select(CustomerWechatAccount).where(CustomerWechatAccount.unionid == unionid).limit(1)
            )
            union_acc = by_union.scalar_one_or_none()
            if union_acc:
                try:
                    async with db.begin_nested():
                        db.add(
                            CustomerWechatAccount(
                                customer_id=union_acc.customer_id, openid=openid, unionid=unionid
                            )
                        )
                        await db.flush()
                except IntegrityError:
                    pass
                else:
                    return await db.get(Customer, union_acc.customer_id)
                again = (
                    await db.execute(select(CustomerWechatAccount).where(CustomerWechatAccount.openid == openid))
                ).scalar_one_or_none()
                if again:
                    return await db.get(Customer, again.customer_id)

        try:
            async with db.begin_nested():
                customer = Customer(customer_no=generate_no(BIZ_CU), status=1)
                db.add(customer)
                await db.flush()
                db.add(CustomerWechatAccount(customer_id=customer.id, openid=openid, unionid=unionid))
                await db.flush()
                return customer
        except IntegrityError:
            # 并发登录：另一请求已写入同一 openid
            again = (
                await db.execute(select(CustomerWechatAccount).where(CustomerWechatAccount.openid == openid))
            ).scalar_one_or_none()
            if again:
                return await db.get(Customer, again.customer_id)
            raise AppException(40002, "微信登录冲突，请重试")

    @staticmethod
    async def get_or_create_by_alipay(db: AsyncSession, alipay_user_id: str) -> Customer:
        result = await db.execute(
            select(CustomerAlipayAccount).where(CustomerAlipayAccount.alipay_user_id == alipay_user_id)
        )
        account = result.scalar_one_or_none()
        if account:
            return await db.get(Customer, account.customer_id)
        try:
            async with db.begin_nested():
                customer = Customer(customer_no=generate_no(BIZ_CU), status=1)
                db.add(customer)
                await db.flush()
                db.add(CustomerAlipayAccount(customer_id=customer.id, alipay_user_id=alipay_user_id))
                await db.flush()
                return customer
        except IntegrityError:
            again = (
                await db.execute(
                    select(CustomerAlipayAccount).where(CustomerAlipayAccount.alipay_user_id == alipay_user_id)
                )
            ).scalar_one_or_none()
            if again:
                return await db.get(Customer, again.customer_id)
            raise AppException(40002, "支付宝登录冲突，请重试")

    @staticmethod
    async def try_link_by_phone(db: AsyncSession, customer: Customer, phone: str) -> Customer:
        """注册填手机号时：若已有同号顾客，合并渠道账号到老顾客，避免微信/支付宝各建一套。"""
        phone = (phone or "").strip()
        if not phone or len(phone) < 7:
            return customer
        other = await db.execute(
            select(Customer).where(Customer.phone == phone, Customer.id != customer.id).limit(1)
        )
        target = other.scalar_one_or_none()
        if not target:
            customer.phone = phone
            return customer

        # 把当前顾客的微信/支付宝账号迁到已有顾客
        for Acc, fk in (
            (CustomerWechatAccount, "customer_id"),
            (CustomerAlipayAccount, "customer_id"),
        ):
            rows = (await db.execute(select(Acc).where(getattr(Acc, fk) == customer.id))).scalars().all()
            for row in rows:
                row.customer_id = target.id
        if not target.nickname and customer.nickname:
            target.nickname = customer.nickname
            target.display_name = target.display_name or customer.display_name or customer.nickname
        if not target.avatar_url and customer.avatar_url:
            target.avatar_url = customer.avatar_url
        target.phone = phone
        # 软禁用被合并的空壳顾客
        customer.status = 0
        customer.phone = None
        customer.token_version = int(getattr(customer, "token_version", 0) or 0) + 1
        await db.flush()
        return target


class OrderCreateService:
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
    ) -> Order:
        from sqlalchemy.exc import IntegrityError

        from app.services.status_history import record_order_status

        if idempotency_key:
            existing = await db.execute(select(Order).where(Order.idempotency_key == idempotency_key))
            order = existing.scalar_one_or_none()
            if order:
                return order

        table_id = None
        table_no_raw: str | None = None
        table_token = (table_token or "").strip() or None
        if table_token:
            from app.services.table_resolve import resolve_dining_table

            # 有 table_token 时以桌台解析出门店为准，避免前端缓存 store_id 与桌台不一致
            table = await resolve_dining_table(db, table_token)
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

        dtype = (dining_type or "").strip().upper()
        if dtype not in ("DINE_IN", "TAKEAWAY"):
            dtype = "TAKEAWAY" if need_pack else "DINE_IN"
        pack = need_pack or dtype == "TAKEAWAY"
        t_no, t_ph = resolve_table_parts(table_no_raw, channel=channel, dining_type=dtype)
        store = await db.get(Store, int(store_id)) if store_id else None
        sno = store.store_no if store else None

        order = Order(
            order_no=generate_no(BIZ_OT, sno, t_no, table_placeholder=t_ph),
            customer_id=customer_id,
            table_id=table_id,
            store_id=store_id,
            status="PENDING_PAYMENT",
            payment_status="UNPAID",
            channel=channel,
            dining_type=dtype,
            remark=remark,
            idempotency_key=idempotency_key,
            ordered_at=now_local(),
        )
        db.add(order)
        try:
            await db.flush()
        except IntegrityError:
            # 并发幂等：唯一约束冲突后返回已有订单
            await db.rollback()
            if idempotency_key:
                existing = await db.execute(select(Order).where(Order.idempotency_key == idempotency_key))
                order = existing.scalar_one_or_none()
                if order:
                    return order
            raise AppException(40901, "订单创建冲突，请重试")

        subtotal, packing_fee, discountable_subtotal = await OrderPriceCalculator.build_order_items(
            db, order, items, deduct_stock=True, need_pack=pack
        )
        rate = await CustomerService.resolve_member_discount_rate(db, customer_id)
        discount_amount, base_total = CustomerService.calc_member_discount(
            subtotal, packing_fee, rate, discountable_subtotal=discountable_subtotal
        )
        coupon_eligible = CustomerService.calc_coupon_eligible_subtotal(
            subtotal, discountable_subtotal, discount_amount
        )
        from app.services.coupon_service import CouponService

        coupon_discount, coupon_id = await CouponService.resolve_discount(
            db, customer_coupon_id, customer_id, coupon_eligible
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
            await CouponService.reserve_for_order(
                db, coupon_id, order_id=order.id, customer_id=customer_id
            )
        if voucher_id:
            await CouponService.reserve_for_order(
                db, voucher_id, order_id=order.id, customer_id=customer_id
            )
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
    ) -> Order:
        from app.services.order_lifecycle import apply_order_status

        order = await db.get(Order, order_id)
        if not order:
            raise AppException(40404, "订单不存在")
        return await apply_order_status(
            db,
            order,
            status,
            source="STAFF",
            operator_id=operator_id,
            assert_kind="STAFF",
        )


class PreferenceService:
    # 相对阈值阈值过高会导致推荐为空；改为 Top-N + 最低出现次数
    HIGH_PREFERENCE_THRESHOLD = Decimal("0.25")
    MIN_ORDER_COUNT = 2
    TOP_N = 8

    @staticmethod
    async def update_from_order(db: AsyncSession, order_id: int) -> None:
        order = await db.get(Order, order_id)
        if not order or not order.customer_id or order.status not in ("PAID", "COMPLETED"):
            return

        # 批量加载明细与规格，消除 N+1
        items = (await db.execute(select(OrderItem).where(OrderItem.order_id == order_id))).scalars().all()
        item_ids = [i.id for i in items]
        if not item_ids:
            return
        opts = (
            await db.execute(select(OrderItemOption).where(OrderItemOption.order_item_id.in_(item_ids)))
        ).scalars().all()

        for opt in opts:
            pref_result = await db.execute(
                select(CustomerPreference).where(
                    CustomerPreference.customer_id == order.customer_id,
                    CustomerPreference.category == opt.group_name_snapshot,
                    CustomerPreference.option_code == opt.option_code,
                )
            )
            pref = pref_result.scalar_one_or_none()
            if pref:
                pref.order_count += 1
                pref.last_used_at = now_local()
                if opt.option_name_snapshot:
                    pref.option_name = opt.option_name_snapshot
            else:
                db.add(
                    CustomerPreference(
                        customer_id=order.customer_id,
                        category=opt.group_name_snapshot,
                        option_code=opt.option_code,
                        option_name=opt.option_name_snapshot,
                        order_count=1,
                        last_used_at=now_local(),
                    )
                )

        total_orders = await db.scalar(
            select(func.count()).select_from(Order).where(
                Order.customer_id == order.customer_id,
                Order.status.in_(("PAID", "CONFIRMED", "PREPARING", "COOKING", "READY", "COMPLETED")),
            )
        ) or 1

        prefs_result = await db.execute(
            select(CustomerPreference).where(CustomerPreference.customer_id == order.customer_id)
        )
        for pref in prefs_result.scalars().all():
            pref.preference_score = money(Decimal(pref.order_count) / Decimal(total_orders))

        customer = await db.get(Customer, order.customer_id)
        if customer:
            customer.last_order_at = now_local()

    @staticmethod
    async def get_recommendations(db: AsyncSession, customer_id: int) -> dict:
        prefs_result = await db.execute(
            select(CustomerPreference)
            .where(
                CustomerPreference.customer_id == customer_id,
                CustomerPreference.order_count >= PreferenceService.MIN_ORDER_COUNT,
            )
            .order_by(CustomerPreference.preference_score.desc(), CustomerPreference.order_count.desc())
            .limit(PreferenceService.TOP_N)
        )
        prefs = list(prefs_result.scalars().all())
        # 若无达到阈值的，退回 Top-N 不限次数
        if not prefs:
            prefs = list(
                (
                    await db.execute(
                        select(CustomerPreference)
                        .where(CustomerPreference.customer_id == customer_id)
                        .order_by(CustomerPreference.preference_score.desc())
                        .limit(PreferenceService.TOP_N)
                    )
                ).scalars().all()
            )
        high = [p for p in prefs if p.preference_score >= PreferenceService.HIGH_PREFERENCE_THRESHOLD]
        use = high or prefs
        return {
            "preferences": [
                {
                    "category": p.category,
                    "option_code": p.option_code,
                    "option_name": p.option_name or p.option_code,
                    "score": float(p.preference_score),
                    "order_count": p.order_count,
                    "label": f"{p.category}·{(p.option_name or p.option_code)}"
                    if p.category
                    else str(p.option_name or p.option_code),
                }
                for p in use
            ]
        }

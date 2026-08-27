from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import generate_no
from app.core.exceptions import AppException
from app.core.response import money, now_local
from app.models import (
    Customer,
    CustomerAlipayAccount,
    CustomerPreference,
    CustomerWechatAccount,
    DiningTable,
    Order,
    OrderItem,
    OrderItemOption,
)
from app.services.order_service import OrderPriceCalculator


class CustomerService:
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
                db.add(CustomerWechatAccount(customer_id=union_acc.customer_id, openid=openid, unionid=unionid))
                await db.flush()
                return await db.get(Customer, union_acc.customer_id)

        customer = Customer(customer_no=generate_no("C"), status=1)
        db.add(customer)
        await db.flush()
        db.add(CustomerWechatAccount(customer_id=customer.id, openid=openid, unionid=unionid))
        await db.flush()
        return customer

    @staticmethod
    async def get_or_create_by_alipay(db: AsyncSession, alipay_user_id: str) -> Customer:
        result = await db.execute(
            select(CustomerAlipayAccount).where(CustomerAlipayAccount.alipay_user_id == alipay_user_id)
        )
        account = result.scalar_one_or_none()
        if account:
            return await db.get(Customer, account.customer_id)
        customer = Customer(customer_no=generate_no("C"), status=1)
        db.add(customer)
        await db.flush()
        db.add(CustomerAlipayAccount(customer_id=customer.id, alipay_user_id=alipay_user_id))
        await db.flush()
        return customer

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
    ) -> Order:
        from sqlalchemy.exc import IntegrityError

        from app.services.status_history import record_order_status

        if idempotency_key:
            existing = await db.execute(select(Order).where(Order.idempotency_key == idempotency_key))
            order = existing.scalar_one_or_none()
            if order:
                return order

        table_id = None
        table_token = (table_token or "").strip() or None
        if table_token:
            from app.services.table_resolve import resolve_dining_table

            table = await resolve_dining_table(db, table_token, store_id=store_id)
            if not table:
                raise AppException(40403, "无效桌台二维码（请扫桌码，或确认页选择桌号）")
            table_id = table.id
            store_id = table.store_id
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

        order = Order(
            order_no=generate_no("ORD"),
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

        subtotal, packing_fee = await OrderPriceCalculator.build_order_items(
            db, order, items, deduct_stock=True, need_pack=pack
        )
        order.subtotal = subtotal
        order.discount_amount = Decimal("0.00")
        order.packing_fee = packing_fee
        order.total_amount = money(subtotal + packing_fee)
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
        from app.services.order_state_machine import assert_staff_transition
        from app.services.status_history import record_order_status
        from app.services.stock_service import restore_stock

        order = await db.get(Order, order_id)
        if not order:
            raise AppException(40404, "订单不存在")
        target = status.strip().upper()
        assert_staff_transition(order.status, target)
        already_completed = order.status == "COMPLETED"
        old = order.status
        order.status = target
        if target == "COMPLETED" and not already_completed:
            order.completed_at = now_local()
            await PreferenceService.update_from_order(db, order_id)
        elif target == "CANCELLED" and old != "CANCELLED":
            order.cancelled_at = now_local()
            # 回滚库存（未支付或已支付取消都回滚销量/库存）
            items = (await db.execute(select(OrderItem).where(OrderItem.order_id == order_id))).scalars().all()
            for it in items:
                await restore_stock(db, it.product_id, it.quantity, store_id=order.store_id)
        await record_order_status(
            db,
            order_id=order.id,
            from_status=old,
            to_status=target,
            source="STAFF",
            operator_id=operator_id,
        )
        await db.flush()
        return order


class PreferenceService:
    # 相对阈值阈值过高会导致推荐为空；改为 Top-N + 最低出现次数
    HIGH_PREFERENCE_THRESHOLD = Decimal("0.25")
    MIN_ORDER_COUNT = 2
    TOP_N = 8

    @staticmethod
    async def update_from_order(db: AsyncSession, order_id: int) -> None:
        order = await db.get(Order, order_id)
        if not order or not order.customer_id or order.status != "COMPLETED":
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
                Order.customer_id == order.customer_id, Order.status == "COMPLETED"
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

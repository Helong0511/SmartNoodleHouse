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
            return await db.get(Customer, account.customer_id)
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
    ) -> Order:
        from sqlalchemy.exc import IntegrityError

        from app.services.status_history import record_order_status

        if idempotency_key:
            existing = await db.execute(select(Order).where(Order.idempotency_key == idempotency_key))
            order = existing.scalar_one_or_none()
            if order:
                return order

        table_id = None
        if table_token:
            result = await db.execute(
                select(DiningTable).where(DiningTable.qr_token == table_token, DiningTable.status == 1)
            )
            table = result.scalar_one_or_none()
            if not table:
                raise AppException(40403, "无效桌台二维码")
            table_id = table.id
            store_id = table.store_id
        else:
            # 禁止客户端随意指定门店占库存：必须桌台令牌或显式合法门店且 >0
            if not store_id or int(store_id) <= 0:
                raise AppException(40008, "缺少桌台令牌或有效门店")

        order = Order(
            order_no=generate_no("ORD"),
            customer_id=customer_id,
            table_id=table_id,
            store_id=store_id,
            status="PENDING_PAYMENT",
            payment_status="UNPAID",
            channel=channel,
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

        subtotal = await OrderPriceCalculator.build_order_items(db, order, items, deduct_stock=True)
        order.subtotal = subtotal
        order.discount_amount = Decimal("0.00")
        order.total_amount = subtotal
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
                await restore_stock(db, it.product_id, it.quantity)
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
            else:
                db.add(
                    CustomerPreference(
                        customer_id=order.customer_id,
                        category=opt.group_name_snapshot,
                        option_code=opt.option_code,
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
                {"category": p.category, "option_code": p.option_code, "score": float(p.preference_score)}
                for p in use
            ]
        }

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
        store_id: int,
        table_token: str | None,
        customer_id: int | None,
        items: list[dict],
        remark: str | None = None,
        channel: str | None = None,
        idempotency_key: str | None = None,
    ) -> Order:
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
        await db.flush()

        subtotal = await OrderPriceCalculator.build_order_items(db, order, items)
        order.subtotal = subtotal
        order.discount_amount = Decimal("0.00")
        order.total_amount = subtotal
        await db.flush()
        return order

    @staticmethod
    async def update_status(db: AsyncSession, order_id: int, status: str) -> Order:
        order = await db.get(Order, order_id)
        if not order:
            raise AppException(40404, "订单不存在")
        order.status = status
        if status == "COMPLETED":
            order.completed_at = now_local()
            await PreferenceService.update_from_order(db, order_id)
        elif status == "CANCELLED":
            order.cancelled_at = now_local()
        await db.flush()
        return order


class PreferenceService:
    HIGH_PREFERENCE_THRESHOLD = Decimal("0.70")

    @staticmethod
    async def update_from_order(db: AsyncSession, order_id: int) -> None:
        order = await db.get(Order, order_id)
        if not order or not order.customer_id or order.status != "COMPLETED":
            return

        items_result = await db.execute(select(OrderItem).where(OrderItem.order_id == order_id))
        items = items_result.scalars().all()
        for item in items:
            opts_result = await db.execute(
                select(OrderItemOption).where(OrderItemOption.order_item_id == item.id)
            )
            for opt in opts_result.scalars().all():
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
                    pref = CustomerPreference(
                        customer_id=order.customer_id,
                        category=opt.group_name_snapshot,
                        option_code=opt.option_code,
                        order_count=1,
                        last_used_at=now_local(),
                    )
                    db.add(pref)

        # 重新计算偏好分数
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

        if order.customer_id:
            customer = await db.get(Customer, order.customer_id)
            if customer:
                customer.last_order_at = now_local()

    @staticmethod
    async def get_recommendations(db: AsyncSession, customer_id: int) -> dict:
        prefs_result = await db.execute(
            select(CustomerPreference)
            .where(
                CustomerPreference.customer_id == customer_id,
                CustomerPreference.preference_score >= PreferenceService.HIGH_PREFERENCE_THRESHOLD,
            )
            .order_by(CustomerPreference.preference_score.desc())
        )
        prefs = prefs_result.scalars().all()
        return {
            "preferences": [
                {"category": p.category, "option_code": p.option_code, "score": float(p.preference_score)}
                for p in prefs
            ]
        }

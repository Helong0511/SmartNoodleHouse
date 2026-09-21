"""会员储值：本金/赠送金入账、扣款（赠送金优先）、调账。"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import money, now_local
from app.models import BalanceLedger, Customer, RechargeOrder


def _wallet_total(c: Customer) -> Decimal:
    return money(getattr(c, "balance_principal", 0) or 0) + money(getattr(c, "balance_gift", 0) or 0)


class BalanceService:
    @staticmethod
    def snapshot(customer: Customer) -> dict:
        principal = money(getattr(customer, "balance_principal", 0) or 0)
        gift = money(getattr(customer, "balance_gift", 0) or 0)
        return {
            "balance_principal": str(principal),
            "balance_gift": str(gift),
            "balance_total": str(principal + gift),
        }

    @classmethod
    async def credit_topup(
        cls,
        db: AsyncSession,
        *,
        recharge: RechargeOrder,
        payment_id: int | None = None,
    ) -> BalanceLedger | None:
        """充值成功入账（幂等：同一 recharge_order 仅一条 TOPUP）。"""
        if not recharge or not recharge.customer_id:
            return None
        existing = (
            await db.execute(
                select(BalanceLedger)
                .where(
                    BalanceLedger.entry_type == "TOPUP",
                    BalanceLedger.recharge_order_id == recharge.id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            return existing

        cust = (
            await db.execute(select(Customer).where(Customer.id == recharge.customer_id).with_for_update())
        ).scalar_one()
        dp = money(recharge.credit_principal)
        dg = money(recharge.credit_gift)
        cust.balance_principal = money(cust.balance_principal or 0) + dp
        cust.balance_gift = money(cust.balance_gift or 0) + dg
        row = BalanceLedger(
            customer_id=cust.id,
            store_id=recharge.store_id,
            recharge_order_id=recharge.id,
            payment_id=payment_id,
            entry_type="TOPUP",
            delta_principal=dp,
            delta_gift=dg,
            principal_after=money(cust.balance_principal),
            gift_after=money(cust.balance_gift),
            remark=f"recharge_no={recharge.recharge_no}",
        )
        db.add(row)
        await db.flush()
        return row

    @classmethod
    async def debit_for_order(
        cls,
        db: AsyncSession,
        *,
        customer_id: int,
        order_id: int,
        amount: Decimal,
        store_id: int | None = None,
        payment_id: int | None = None,
    ) -> BalanceLedger:
        """余额支付扣款：赠送金优先，不足再扣本金。幂等按 order_id。"""
        pay = money(amount)
        if pay <= 0:
            raise AppException(40051, "余额扣款金额非法")

        existing = (
            await db.execute(
                select(BalanceLedger)
                .where(BalanceLedger.entry_type == "PAY", BalanceLedger.order_id == order_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            return existing

        cust = (
            await db.execute(select(Customer).where(Customer.id == customer_id).with_for_update())
        ).scalar_one_or_none()
        if not cust:
            raise AppException(40401, "顾客不存在")
        gift = money(cust.balance_gift or 0)
        principal = money(cust.balance_principal or 0)
        if gift + principal < pay:
            raise AppException(40090, f"储值余额不足，当前可用 ¥{gift + principal}")

        use_gift = min(gift, pay)
        use_principal = pay - use_gift
        cust.balance_gift = gift - use_gift
        cust.balance_principal = principal - use_principal
        row = BalanceLedger(
            customer_id=cust.id,
            store_id=store_id,
            order_id=order_id,
            payment_id=payment_id,
            entry_type="PAY",
            delta_principal=-use_principal,
            delta_gift=-use_gift,
            principal_after=money(cust.balance_principal),
            gift_after=money(cust.balance_gift),
            remark=f"order_id={order_id}",
        )
        db.add(row)
        await db.flush()
        return row

    @classmethod
    async def restore_for_refund(
        cls,
        db: AsyncSession,
        *,
        customer_id: int,
        order_id: int,
        refund_id: int,
        amount: Decimal,
        store_id: int | None = None,
    ) -> BalanceLedger | None:
        """余额支付退款回补：按原 PAY 流水中本金/赠送比例回退；无原流水则全额回本金。"""
        amt = money(amount)
        if amt <= 0:
            return None
        existing = (
            await db.execute(
                select(BalanceLedger).where(BalanceLedger.refund_id == refund_id).limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            return existing

        pay_row = (
            await db.execute(
                select(BalanceLedger)
                .where(BalanceLedger.entry_type == "PAY", BalanceLedger.order_id == order_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if pay_row:
            paid_total = money(abs(pay_row.delta_principal) + abs(pay_row.delta_gift))
            if paid_total > 0:
                gift_share = money(abs(pay_row.delta_gift) * amt / paid_total)
                principal_share = amt - gift_share
            else:
                gift_share = Decimal("0.00")
                principal_share = amt
        else:
            gift_share = Decimal("0.00")
            principal_share = amt

        cust = (
            await db.execute(select(Customer).where(Customer.id == customer_id).with_for_update())
        ).scalar_one()
        cust.balance_principal = money(cust.balance_principal or 0) + principal_share
        cust.balance_gift = money(cust.balance_gift or 0) + gift_share
        row = BalanceLedger(
            customer_id=cust.id,
            store_id=store_id,
            order_id=order_id,
            refund_id=refund_id,
            entry_type="REFUND_RESTORE",
            delta_principal=principal_share,
            delta_gift=gift_share,
            principal_after=money(cust.balance_principal),
            gift_after=money(cust.balance_gift),
            remark=f"refund_id={refund_id}",
        )
        db.add(row)
        await db.flush()
        return row

    @classmethod
    async def adjust(
        cls,
        db: AsyncSession,
        *,
        customer_id: int,
        delta_principal: Decimal = Decimal("0"),
        delta_gift: Decimal = Decimal("0"),
        store_id: int | None = None,
        remark: str | None = None,
        operator_id: int | None = None,
    ) -> BalanceLedger:
        dp = money(delta_principal)
        dg = money(delta_gift)
        if dp == 0 and dg == 0:
            raise AppException(40051, "调账金额不能为 0")
        cust = (
            await db.execute(select(Customer).where(Customer.id == customer_id).with_for_update())
        ).scalar_one_or_none()
        if not cust:
            raise AppException(40401, "顾客不存在")
        new_p = money(cust.balance_principal or 0) + dp
        new_g = money(cust.balance_gift or 0) + dg
        if new_p < 0 or new_g < 0:
            raise AppException(40091, "调账后余额不能为负")
        cust.balance_principal = new_p
        cust.balance_gift = new_g
        row = BalanceLedger(
            customer_id=cust.id,
            store_id=store_id,
            operator_id=int(operator_id) if operator_id else None,
            entry_type="ADJUST",
            delta_principal=dp,
            delta_gift=dg,
            principal_after=new_p,
            gift_after=new_g,
            remark=(remark or "人工调账")[:255],
        )
        db.add(row)
        await db.flush()
        return row

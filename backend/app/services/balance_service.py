"""会员储值：本金/赠送金入账、扣款（赠送金优先）、调账。"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.biz_no import generate_no
from app.core.exceptions import AppException
from app.core.response import money
from app.models import BalanceLedger, Customer, RechargeOrder, Store


def _wallet_total(c: Customer) -> Decimal:
    return money(getattr(c, "balance_principal", 0) or 0) + money(getattr(c, "balance_gift", 0) or 0)


async def _store_no(db: AsyncSession, store_id: int | None) -> str | None:
    if not store_id:
        return None
    store = await db.get(Store, int(store_id))
    return (store.store_no if store else None) or f"S{store_id}"


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
        """充值成功入账（幂等：同一 recharge_order 仅一条 TOPUP）。操作人=顾客本人。"""
        if not recharge or not recharge.customer_id:
            return None
        # 先锁顾客，再对流水做锁定读。普通 SELECT 用事务快照，并发入账会把已提交流水看成不存在。
        cust = (
            await db.execute(select(Customer).where(Customer.id == recharge.customer_id).with_for_update())
        ).scalar_one()
        existing = (
            await db.execute(
                select(BalanceLedger)
                .where(
                    BalanceLedger.entry_type == "TOPUP",
                    BalanceLedger.recharge_order_id == recharge.id,
                )
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if existing:
            return existing
        dp = money(recharge.credit_principal)
        dg = money(recharge.credit_gift)
        cust.balance_principal = money(cust.balance_principal or 0) + dp
        cust.balance_gift = money(cust.balance_gift or 0) + dg
        row = BalanceLedger(
            customer_id=cust.id,
            store_id=recharge.store_id,
            recharge_order_id=recharge.id,
            payment_id=payment_id,
            # 顾客自助充值：无员工 operator_id；操作人展示走顾客本人
            biz_no=recharge.recharge_no,
            entry_type="TOPUP",
            delta_principal=dp,
            delta_gift=dg,
            principal_after=money(cust.balance_principal),
            gift_after=money(cust.balance_gift),
            remark=f"顾客自助充值 recharge_no={recharge.recharge_no}",
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
        order_no: str | None = None,
    ) -> BalanceLedger:
        """余额支付扣款：赠送金优先，不足再扣本金。幂等按 order_id。操作人=顾客本人。"""
        pay = money(amount)
        if pay <= 0:
            raise AppException(40051, "余额扣款金额非法")

        cust = (
            await db.execute(select(Customer).where(Customer.id == customer_id).with_for_update())
        ).scalar_one_or_none()
        if not cust:
            raise AppException(40401, "顾客不存在")
        existing = (
            await db.execute(
                select(BalanceLedger)
                .where(BalanceLedger.entry_type == "PAY", BalanceLedger.order_id == order_id)
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if existing:
            # 幂等命中不代表本次请求参数可信：必须确认同一顾客、订单、支付单及金额，
            # 否则不能把旧扣款流水当作新支付成功返回。
            existing_amount = money(
                abs(existing.delta_principal) + abs(existing.delta_gift)
            )
            if (
                int(existing.customer_id) != int(customer_id)
                or int(existing.order_id or 0) != int(order_id)
                or money(existing_amount) != pay
                or (
                    payment_id is not None
                    and (
                        existing.payment_id is None
                        or int(existing.payment_id) != int(payment_id)
                    )
                )
                or (
                    store_id is not None
                    and (
                        existing.store_id is None
                        or int(existing.store_id) != int(store_id)
                    )
                )
            ):
                raise AppException(40994, "订单已有储值扣款流水，但顾客/金额/支付单不一致，拒绝重复扣款")
            return existing
        from app.services.customer_service import CustomerService

        if not CustomerService.is_member(cust):
            raise AppException(40320, "请先开通会员后再使用余额支付", status_code=403)
        gift = money(cust.balance_gift or 0)
        principal = money(cust.balance_principal or 0)
        if gift + principal < pay:
            raise AppException(40090, f"储值余额不足，当前可用 ¥{gift + principal}")

        use_gift = min(gift, pay)
        use_principal = pay - use_gift
        cust.balance_gift = gift - use_gift
        cust.balance_principal = principal - use_principal
        # 关联单号优先用订单号；无则生成钱包消费单号
        biz = (order_no or "").strip() or None
        if not biz:
            sno = await _store_no(db, store_id)
            biz = generate_no("WP", sno, "00")
        row = BalanceLedger(
            customer_id=cust.id,
            store_id=store_id,
            order_id=order_id,
            payment_id=payment_id,
            biz_no=biz,
            entry_type="PAY",
            delta_principal=-use_principal,
            delta_gift=-use_gift,
            principal_after=money(cust.balance_principal),
            gift_after=money(cust.balance_gift),
            remark=f"顾客余额消费 order_id={order_id}",
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
        cust = (
            await db.execute(select(Customer).where(Customer.id == customer_id).with_for_update())
        ).scalar_one()
        existing = (
            await db.execute(
                select(BalanceLedger)
                .where(BalanceLedger.refund_id == refund_id)
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if existing:
            existing_amount = money(
                abs(existing.delta_principal) + abs(existing.delta_gift)
            )
            if (
                existing.entry_type != "REFUND_RESTORE"
                or int(existing.customer_id) != int(customer_id)
                or int(existing.order_id or 0) != int(order_id)
                or money(existing_amount) != amt
            ):
                raise AppException(40994, "退款编号已关联不同的储值回补流水，拒绝重复入账")
            return existing

        pay_row = (
            await db.execute(
                select(BalanceLedger)
                .where(BalanceLedger.entry_type == "PAY", BalanceLedger.order_id == order_id)
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if pay_row:
            paid_principal = money(abs(pay_row.delta_principal))
            paid_gift = money(abs(pay_row.delta_gift))
            paid_total = money(paid_principal + paid_gift)
            if paid_total <= 0:
                raise AppException(40991, "原储值支付流水金额无效，拒绝自动回补")
            # 分笔退款不能逐笔独立四舍五入 gift 占比，否则多笔退款累加可能产生分币漂移。
            # 按“累计已退款金额”计算目标赠送金回补值，本次只补差额；全额退时精确回到原始赠送金。
            previous_restore = (
                await db.execute(
                    select(
                        func.coalesce(func.sum(BalanceLedger.delta_principal), 0),
                        func.coalesce(func.sum(BalanceLedger.delta_gift), 0),
                    ).where(
                        BalanceLedger.entry_type == "REFUND_RESTORE",
                        BalanceLedger.order_id == order_id,
                        BalanceLedger.customer_id == customer_id,
                    )
                )
            ).one()
            previous_principal = money(previous_restore[0] or 0)
            previous_gift = money(previous_restore[1] or 0)
            previous_total = money(previous_principal + previous_gift)
            cumulative_refund = money(previous_total + amt)
            if cumulative_refund > paid_total:
                raise AppException(40992, "累计余额退款超过原储值支付金额，拒绝重复回补")
            target_gift = (
                paid_gift
                if cumulative_refund == paid_total
                else money(paid_gift * cumulative_refund / paid_total)
            )
            gift_share = money(target_gift - previous_gift)
            principal_share = money(amt - gift_share)
            if gift_share < 0 or principal_share < 0:
                raise AppException(40993, "余额退款本金/赠送金分摊异常，需人工核对")
        else:
            # 历史订单没有 PAY 流水时，沿用旧兼容策略：全部回补本金。
            gift_share = Decimal("0.00")
            principal_share = amt

        cust.balance_principal = money(cust.balance_principal or 0) + principal_share
        cust.balance_gift = money(cust.balance_gift or 0) + gift_share
        sno = await _store_no(db, store_id)
        row = BalanceLedger(
            customer_id=cust.id,
            store_id=store_id,
            order_id=order_id,
            refund_id=refund_id,
            biz_no=generate_no("WR", sno, "00"),
            entry_type="REFUND_RESTORE",
            delta_principal=principal_share,
            delta_gift=gift_share,
            principal_after=money(cust.balance_principal),
            gift_after=money(cust.balance_gift),
            remark=f"余额退款回补 refund_id={refund_id}",
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
        sno = await _store_no(db, store_id)
        adjust_no = generate_no("WA", sno, "00")
        row = BalanceLedger(
            customer_id=cust.id,
            store_id=store_id,
            operator_id=int(operator_id) if operator_id else None,
            biz_no=adjust_no,
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

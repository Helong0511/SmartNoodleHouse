"""统一财务分录入账（收款 SALE / 退款 REFUND）。"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import money
from app.models import FinancialLedger


class FinancialLedgerService:
    """写入 financial_ledger；按 payment_id / refund_id / delivery_* 幂等。"""

    @classmethod
    async def record_sale(
        cls,
        db: AsyncSession,
        *,
        store_id: int,
        amount: Decimal | str | float | int,
        channel: str | None,
        provider: str | None,
        order_id: int | None,
        payment_id: int | None = None,
        delivery_order_id: int | None = None,
        remark: str | None = None,
    ) -> FinancialLedger | None:
        amt = money(amount)
        if amt <= 0:
            return None
        if payment_id is not None:
            existing = (
                await db.execute(
                    select(FinancialLedger).where(
                        FinancialLedger.entry_type == "SALE",
                        FinancialLedger.payment_id == payment_id,
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if existing:
                return existing
        if payment_id is None and delivery_order_id is not None:
            existing = (
                await db.execute(
                    select(FinancialLedger).where(
                        FinancialLedger.entry_type == "SALE",
                        FinancialLedger.delivery_order_id == delivery_order_id,
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if existing:
                return existing

        row = FinancialLedger(
            store_id=int(store_id),
            entry_type="SALE",
            channel=str(channel).upper()[:30] if channel else None,
            provider=str(provider).upper()[:20] if provider else None,
            amount=amt,
            currency="CNY",
            order_id=order_id,
            payment_id=payment_id,
            delivery_order_id=delivery_order_id,
            remark=str(remark)[:500] if remark else None,
        )
        db.add(row)
        await db.flush()
        return row

    @classmethod
    async def record_refund(
        cls,
        db: AsyncSession,
        *,
        store_id: int,
        amount: Decimal | str | float | int,
        channel: str | None,
        provider: str | None,
        order_id: int | None,
        payment_id: int | None = None,
        refund_id: int | None = None,
        delivery_order_id: int | None = None,
        delivery_refund_id: int | None = None,
        remark: str | None = None,
    ) -> FinancialLedger | None:
        amt = money(amount)
        if amt <= 0:
            return None
        if refund_id is not None:
            existing = (
                await db.execute(
                    select(FinancialLedger).where(
                        FinancialLedger.entry_type == "REFUND",
                        FinancialLedger.refund_id == refund_id,
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if existing:
                return existing
        if delivery_refund_id is not None:
            existing = (
                await db.execute(
                    select(FinancialLedger).where(
                        FinancialLedger.entry_type == "REFUND",
                        FinancialLedger.delivery_refund_id == delivery_refund_id,
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if existing:
                return existing

        row = FinancialLedger(
            store_id=int(store_id),
            entry_type="REFUND",
            channel=str(channel).upper()[:30] if channel else None,
            provider=str(provider).upper()[:20] if provider else None,
            amount=amt,
            currency="CNY",
            order_id=order_id,
            payment_id=payment_id,
            refund_id=refund_id,
            delivery_order_id=delivery_order_id,
            delivery_refund_id=delivery_refund_id,
            remark=str(remark)[:500] if remark else None,
        )
        db.add(row)
        await db.flush()
        return row

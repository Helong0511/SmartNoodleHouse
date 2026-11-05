"""统一财务分录入账（收款 SALE / 退款 REFUND）。"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import money
from app.models import FinancialLedger


async def _flush_idempotent(db: AsyncSession, row: FinancialLedger) -> FinancialLedger:
    """SAVEPOINT 内 flush；并发唯一约束冲突时回退 SAVEPOINT 并返回已有行（DB 级幂等兜底）。"""
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
        return row
    except IntegrityError:
        existing = (
            await db.execute(
                select(FinancialLedger).where(
                    FinancialLedger.entry_type == row.entry_type,
                    FinancialLedger.payment_id == row.payment_id,
                    FinancialLedger.refund_id == row.refund_id,
                    FinancialLedger.delivery_order_id == row.delivery_order_id,
                    FinancialLedger.delivery_refund_id == row.delivery_refund_id,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            return _assert_same_ledger(existing, row)
        raise


def _assert_same_ledger(existing: FinancialLedger, expected: FinancialLedger) -> FinancialLedger:
    """同一幂等业务键只能对应完全一致的财务事实，禁止静默接受金额/门店/渠道漂移。"""
    fields = (
        "store_id",
        "entry_type",
        "channel",
        "provider",
        "currency",
        "order_id",
        "payment_id",
        "refund_id",
        "delivery_order_id",
        "delivery_refund_id",
    )
    mismatch = [name for name in fields if getattr(existing, name) != getattr(expected, name)]
    if money(existing.amount) != money(expected.amount):
        mismatch.append("amount")
    if mismatch:
        raise AppException(
            40996,
            "财务幂等键对应的分录内容不一致，已阻止重复覆盖；请人工核对字段: "
            + ",".join(mismatch[:8]),
        )
    return existing


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
                expected = FinancialLedger(
                    store_id=int(store_id), entry_type="SALE",
                    channel=str(channel).upper()[:30] if channel else None,
                    provider=str(provider).upper()[:20] if provider else None,
                    amount=amt, currency="CNY", order_id=order_id,
                    payment_id=payment_id, delivery_order_id=delivery_order_id,
                )
                return _assert_same_ledger(existing, expected)
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
                expected = FinancialLedger(
                    store_id=int(store_id), entry_type="SALE",
                    channel=str(channel).upper()[:30] if channel else None,
                    provider=str(provider).upper()[:20] if provider else None,
                    amount=amt, currency="CNY", order_id=order_id,
                    payment_id=payment_id, delivery_order_id=delivery_order_id,
                )
                return _assert_same_ledger(existing, expected)

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
        return await _flush_idempotent(db, row)

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
                expected = FinancialLedger(
                    store_id=int(store_id), entry_type="REFUND",
                    channel=str(channel).upper()[:30] if channel else None,
                    provider=str(provider).upper()[:20] if provider else None,
                    amount=amt, currency="CNY", order_id=order_id,
                    payment_id=payment_id, refund_id=refund_id,
                    delivery_order_id=delivery_order_id, delivery_refund_id=delivery_refund_id,
                )
                return _assert_same_ledger(existing, expected)
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
                expected = FinancialLedger(
                    store_id=int(store_id), entry_type="REFUND",
                    channel=str(channel).upper()[:30] if channel else None,
                    provider=str(provider).upper()[:20] if provider else None,
                    amount=amt, currency="CNY", order_id=order_id,
                    payment_id=payment_id, refund_id=refund_id,
                    delivery_order_id=delivery_order_id, delivery_refund_id=delivery_refund_id,
                )
                return _assert_same_ledger(existing, expected)

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
        return await _flush_idempotent(db, row)

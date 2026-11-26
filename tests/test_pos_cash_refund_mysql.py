"""MySQL concurrency/accounting regressions for POS cash refunds."""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.core.response import now_local
from app.models import (
    CashierShift,
    Order,
    Payment,
    PaymentRefund,
    Role,
    Store,
    User,
)
from app.services.pos_shift_service import PosShiftService


async def _make_shift_graph(maker, suffix: str):
    async with maker() as db:
        store = Store(store_no=f"CR-{suffix[:20]}", name="Cash refund shift test")
        role = Role(code=f"CR_{suffix[:12]}", name="Cash refund test role")
        db.add_all([store, role])
        await db.flush()
        user = User(
            username=f"cr_{suffix[:20]}",
            password_hash="not-a-real-password-hash",
            display_name="Cash refund test cashier",
            role_id=role.id,
            store_id=store.id,
            status=1,
        )
        db.add(user)
        await db.flush()
        order = Order(
            order_no=f"CRO-{suffix[:28]}",
            store_id=store.id,
            status="COMPLETED",
            payment_status="PAID",
            subtotal="50.00",
            total_amount="50.00",
            discount_amount="0.00",
            coupon_discount="0.00",
            voucher_discount="0.00",
            packing_fee="0.00",
            dining_type="TAKEOUT",
            channel="POS",
        )
        db.add(order)
        await db.flush()
        shift = CashierShift(
            store_id=store.id,
            operator_id=user.id,
            opening_float="100.00",
            status="OPEN",
            opened_at=now_local() - timedelta(minutes=10),
        )
        db.add(shift)
        await db.flush()
        result = {
            "store_id": store.id,
            "role_id": role.id,
            "user_id": user.id,
            "order_id": order.id,
            "shift_id": shift.id,
        }
        await db.commit()
        return result


async def _cleanup(maker, ids: dict, payment_ids: list[int], refund_ids: list[int]):
    async with maker() as db:
        if refund_ids:
            await db.execute(delete(PaymentRefund).where(PaymentRefund.id.in_(refund_ids)))
        if payment_ids:
            await db.execute(delete(Payment).where(Payment.id.in_(payment_ids)))
        await db.execute(delete(Order).where(Order.id == ids["order_id"]))
        await db.execute(delete(CashierShift).where(CashierShift.id == ids["shift_id"]))
        await db.execute(delete(User).where(User.id == ids["user_id"]))
        await db.execute(delete(Role).where(Role.id == ids["role_id"]))
        await db.execute(delete(Store).where(Store.id == ids["store_id"]))
        await db.commit()


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_cash_refund_reduces_only_cash_drawer_and_is_in_shift_report(db_session):
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")
    maker = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    ids = await _make_shift_graph(maker, suffix)
    payment_ids: list[int] = []
    refund_ids: list[int] = []
    try:
        async with maker() as db:
            cash = Payment(
                payment_no=f"CRC-{suffix[:28]}",
                order_id=ids["order_id"],
                operator_id=ids["user_id"],
                cashier_shift_id=ids["shift_id"],
                provider="CASH",
                channel="POS",
                amount="30.00",
                status="PAID",
                paid_at=now_local(),
            )
            wechat = Payment(
                payment_no=f"CRW-{suffix[:28]}",
                order_id=ids["order_id"],
                operator_id=ids["user_id"],
                cashier_shift_id=ids["shift_id"],
                provider="WECHAT",
                channel="POS",
                amount="20.00",
                status="PAID",
                paid_at=now_local(),
            )
            db.add_all([cash, wechat])
            await db.flush()
            cash_refund = PaymentRefund(
                refund_no=f"CRRC-{suffix[:24]}",
                payment_id=cash.id,
                order_id=ids["order_id"],
                cashier_shift_id=ids["shift_id"],
                amount="5.00",
                status="SUCCESS",
                operator_id=ids["user_id"],
            )
            wechat_refund = PaymentRefund(
                refund_no=f"CRRW-{suffix[:24]}",
                payment_id=wechat.id,
                order_id=ids["order_id"],
                amount="10.00",
                status="SUCCESS",
                operator_id=ids["user_id"],
            )
            db.add_all([cash_refund, wechat_refund])
            await db.flush()
            payment_ids = [cash.id, wechat.id]
            refund_ids = [cash_refund.id, wechat_refund.id]
            shift = await db.get(CashierShift, ids["shift_id"])
            closed = await PosShiftService.close_shift(
                db,
                shift,
                closing_cash=Decimal("125.00"),
                remark="cash refund accounting regression",
                closed_by=ids["user_id"],
            )
            await db.commit()
            payload = await PosShiftService.shift_payload(db, closed)
            assert Decimal(str(closed.total_cash)) == Decimal("30.00")
            assert Decimal(str(closed.total_wechat)) == Decimal("20.00")
            assert Decimal(str(closed.cash_difference)) == Decimal("0.00")
            assert Decimal(payload["total_cash_refunds"]) == Decimal("5.00")
            assert Decimal(payload["expected_cash"]) == Decimal("125.00")
    finally:
        await _cleanup(maker, ids, payment_ids, refund_ids)


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_shift_close_refuses_unresolved_cash_refund(db_session):
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")
    maker = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    ids = await _make_shift_graph(maker, suffix)
    payment_ids: list[int] = []
    refund_ids: list[int] = []
    try:
        async with maker() as db:
            payment = Payment(
                payment_no=f"CRP-{suffix[:28]}",
                order_id=ids["order_id"],
                operator_id=ids["user_id"],
                cashier_shift_id=ids["shift_id"],
                provider="CASH",
                channel="POS",
                amount="20.00",
                status="PAID",
                paid_at=now_local(),
            )
            db.add(payment)
            await db.flush()
            refund = PaymentRefund(
                refund_no=f"CRPR-{suffix[:24]}",
                payment_id=payment.id,
                order_id=ids["order_id"],
                cashier_shift_id=ids["shift_id"],
                amount="5.00",
                status="PENDING",
                operator_id=ids["user_id"],
            )
            db.add(refund)
            await db.flush()
            payment_ids = [payment.id]
            refund_ids = [refund.id]
            shift = await db.get(CashierShift, ids["shift_id"])
            with pytest.raises(AppException) as exc:
                await PosShiftService.close_shift(
                    db,
                    shift,
                    closing_cash=Decimal("115.00"),
                    remark=None,
                    closed_by=ids["user_id"],
                )
            assert exc.value.code == 40910
            await db.rollback()
    finally:
        await _cleanup(maker, ids, payment_ids, refund_ids)

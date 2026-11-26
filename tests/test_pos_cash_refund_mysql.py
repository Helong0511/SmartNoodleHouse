"""MySQL concurrency/accounting regressions for POS cash refunds."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, event
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
from app.services.payment_service import PaymentService
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


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_cash_refund_and_shift_close_serialize_on_shift_row(db_session):
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")
    maker = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    ids = await _make_shift_graph(maker, suffix)
    payment_ids: list[int] = []
    refund_ids: list[int] = []
    second_close_lock = asyncio.Event()
    async with maker() as refund_session, maker() as close_session:
        try:
            locked = await PosShiftService.require_open_shift_for_update(
                refund_session, ids["user_id"], store_id=ids["store_id"]
            )
            payment = Payment(
                payment_no=f"CRS-{suffix[:28]}",
                order_id=ids["order_id"],
                operator_id=ids["user_id"],
                cashier_shift_id=locked.id,
                provider="CASH",
                channel="POS",
                amount="15.00",
                status="PAID",
                paid_at=now_local(),
            )
            refund_session.add(payment)
            await refund_session.flush()
            refund = PaymentRefund(
                refund_no=f"CRSR-{suffix[:24]}",
                payment_id=payment.id,
                order_id=ids["order_id"],
                cashier_shift_id=locked.id,
                amount="5.00",
                status="PENDING",
                operator_id=ids["user_id"],
            )
            refund_session.add(refund)
            await refund_session.flush()
            payment_ids = [payment.id]
            refund_ids = [refund.id]
            stale_shift = await close_session.get(CashierShift, ids["shift_id"])
            close_conn = (await close_session.connection()).sync_connection

            def observe_close_lock(conn, _cursor, statement, _parameters, _context, _many):
                normalized = statement.upper()
                if (
                    conn is close_conn
                    and "CASHIER_SHIFTS" in normalized
                    and "FOR UPDATE" in normalized
                ):
                    second_close_lock.set()

            event.listen(db_session.bind.sync_engine, "before_cursor_execute", observe_close_lock)
            try:
                close_task = asyncio.create_task(
                    PosShiftService.close_shift(
                        close_session,
                        stale_shift,
                        closing_cash=Decimal("110.00"),
                        remark=None,
                        closed_by=ids["user_id"],
                    )
                )
                await asyncio.wait_for(second_close_lock.wait(), timeout=10)
                await refund_session.commit()
                with pytest.raises(AppException) as exc:
                    await asyncio.wait_for(close_task, timeout=15)
                assert exc.value.code == 40910
            finally:
                event.remove(db_session.bind.sync_engine, "before_cursor_execute", observe_close_lock)
                if refund_session.in_transaction():
                    await refund_session.rollback()
                if close_session.in_transaction():
                    await close_session.rollback()
        finally:
            await _cleanup(maker, ids, payment_ids, refund_ids)


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_payment_service_binds_cash_refund_to_executing_shift_without_early_commit(
    db_session, monkeypatch
):
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
                payment_no=f"CRSVC-{suffix[:24]}",
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
            payment_ids.append(payment.id)

            async def fake_finalize(session, refund_row, new_status, **kwargs):
                refund_row.status = new_status
                refund_row.succeeded_at = now_local()
                return refund_row

            monkeypatch.setattr(
                PaymentService, "_finalize_refund", AsyncMock(side_effect=fake_finalize)
            )
            monkeypatch.setattr(
                "app.services.payment_service._order_table_parts",
                AsyncMock(return_value=(None, None)),
            )
            monkeypatch.setattr(
                "app.services.payment_service._order_store_no",
                AsyncMock(return_value="TEST"),
            )
            refund = await PaymentService.refund(
                db,
                payment,
                Decimal("5.00"),
                "POS cash refund shift attribution",
                operator_id=ids["user_id"],
                idempotency_key=f"cash-refund-{suffix[:24]}",
            )
            refund_ids.append(refund.id)
            assert refund.status == "SUCCESS"
            assert refund.cashier_shift_id == ids["shift_id"]
            # The CASH path must retain the shift row lock until the caller commits
            # the refund/audit unit. A second session's FOR UPDATE must wait.
            async with maker() as competing:
                lock_query_seen = asyncio.Event()
                competing_conn = (await competing.connection()).sync_connection

                def observe_competing_lock(conn, _cursor, statement, _parameters, _context, _many):
                    normalized = statement.upper()
                    if (
                        conn is competing_conn
                        and "CASHIER_SHIFTS" in normalized
                        and "FOR UPDATE" in normalized
                    ):
                        lock_query_seen.set()

                event.listen(db_session.bind.sync_engine, "before_cursor_execute", observe_competing_lock)
                try:
                    lock_task = asyncio.create_task(
                        competing.execute(
                            select(CashierShift)
                            .where(CashierShift.id == ids["shift_id"])
                            .with_for_update()
                        )
                    )
                    await asyncio.wait_for(lock_query_seen.wait(), timeout=5)
                    await asyncio.sleep(0.15)
                    assert not lock_task.done(), "PaymentService.refund released shift lock before caller commit"
                    await db.commit()
                    await asyncio.wait_for(lock_task, timeout=5)
                    await competing.rollback()
                finally:
                    event.remove(
                        db_session.bind.sync_engine,
                        "before_cursor_execute",
                        observe_competing_lock,
                    )
    finally:
        await _cleanup(maker, ids, payment_ids, refund_ids)

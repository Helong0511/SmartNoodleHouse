import inspect

import pytest
from sqlalchemy import select

from app.core.exceptions import AppException
from app.models import Customer, CustomerWriteGate
from app.services.customer_write_gate import (
    lock_active_customer_for_write,
    lock_customer_write_gates,
)


@pytest.mark.asyncio
async def test_customer_orm_insert_creates_write_gate_in_same_transaction(db_session):
    customer = Customer(customer_no="GATE-TEST-0001", status=1, account_status="ACTIVE")
    db_session.add(customer)
    await db_session.flush()

    gate_id = await db_session.scalar(
        select(CustomerWriteGate.customer_id).where(
            CustomerWriteGate.customer_id == customer.id
        )
    )
    assert gate_id == customer.id


@pytest.mark.asyncio
async def test_gate_helper_locks_ids_in_sorted_unique_order(db_session):
    customers = [
        Customer(customer_no="GATE-TEST-0002", status=1, account_status="ACTIVE"),
        Customer(customer_no="GATE-TEST-0003", status=1, account_status="ACTIVE"),
    ]
    db_session.add_all(customers)
    await db_session.flush()

    ids = [int(customers[1].id), int(customers[0].id), int(customers[1].id)]
    assert await lock_customer_write_gates(db_session, ids) == sorted(set(ids))


@pytest.mark.asyncio
async def test_missing_gate_fails_closed(db_session):
    customer = Customer(customer_no="GATE-TEST-0004", status=1, account_status="ACTIVE")
    db_session.add(customer)
    await db_session.flush()
    await db_session.delete(
        await db_session.get(CustomerWriteGate, customer.id)
    )
    await db_session.flush()

    with pytest.raises(AppException) as exc:
        await lock_customer_write_gates(db_session, [customer.id])
    assert exc.value.code == 50021


@pytest.mark.asyncio
async def test_lock_active_customer_rejects_disabled_account(db_session):
    customer = Customer(customer_no="GATE-TEST-0005", status=0, account_status="DISABLED")
    db_session.add(customer)
    await db_session.flush()

    with pytest.raises(AppException):
        await lock_active_customer_for_write(db_session, customer.id)



def test_paid_order_points_award_acquires_gate_before_order_and_customer_locks():
    from app.services.customer_service import CustomerService

    source = inspect.getsource(CustomerService.award_points_for_paid_order)
    gate_at = source.index("await lock_customer_write_gates")
    order_lock_at = source.index(".with_for_update()")
    customer_lock_at = source.index("select(Customer)")
    assert gate_at < order_lock_at < customer_lock_at
    assert "populate_existing=True" in source
    assert "observed_customer_id" in source


def test_points_exchange_acquires_gate_before_customer_lock():
    from app.services.customer_service import CustomerService

    source = inspect.getsource(CustomerService.exchange_points_for_coupon)
    gate_at = source.index("await lock_customer_write_gates")
    customer_lock_at = source.index("select(Customer)")
    assert gate_at < customer_lock_at
    assert ".with_for_update()" in source
    assert "populate_existing=True" in source

"""Customer checkout must serialize order creation with customer merge."""
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api import orders


def test_customer_order_creation_acquires_lifecycle_gate_before_writing_order():
    source = inspect.getsource(orders.create_order)
    gate = source.index("await lock_active_customer_for_write(db, int(customer.id))")
    create = source.index("await OrderCreateService.create_order(")
    assert gate < create


def test_order_idempotency_race_uses_savepoint_not_outer_rollback():
    from app.services import order_create_service

    source = inspect.getsource(order_create_service.OrderCreateService.create_order)
    race = source.index("except IntegrityError:")
    recovery = source[race:source.index("subtotal, packing_fee", race)]
    assert "db.begin_nested()" in source
    assert "await db.rollback()" not in recovery


@pytest.mark.asyncio
async def test_pos_cash_settlement_rejects_customer_owner_change_after_gate_lock():
    from app.core.exceptions import AppException
    from app.services.payment_service import PaymentService

    order = SimpleNamespace(id=1, customer_id=7)
    locked_order = SimpleNamespace(id=1, customer_id=8)
    db = MagicMock()
    db.scalar = AsyncMock(return_value=7)
    result = MagicMock()
    result.scalar_one_or_none.return_value = locked_order
    db.execute = AsyncMock(return_value=result)

    with patch(
        "app.services.payment_service.lock_customer_write_gates",
        new_callable=AsyncMock,
    ) as lock_gates:
        with pytest.raises(AppException) as exc:
            await PaymentService.settle_cash_payment(db, order)

    assert exc.value.code == 40906
    lock_gates.assert_awaited_once_with(db, [7])
    db.execute.assert_awaited_once()


def test_pos_cash_settlement_acquires_customer_gate_before_order_lock():
    from app.services.payment_service import PaymentService

    source = inspect.getsource(PaymentService.settle_cash_payment)
    owner_peek = source.index("select(Order.customer_id)")
    gate = source.index("await lock_customer_write_gates")
    order_lock = source.index(".with_for_update()")
    owner_recheck = source.index("locked_owner_id != observed_owner_id")
    assert owner_peek < gate < order_lock < owner_recheck

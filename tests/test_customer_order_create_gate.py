"""Customer checkout must serialize order creation with customer merge."""
import inspect

from app.api import orders


def test_customer_order_creation_acquires_lifecycle_gate_before_writing_order():
    source = inspect.getsource(orders.create_order)
    gate = source.index("await lock_active_customer_for_write(db, int(customer.id))")
    create = source.index("await OrderCreateService.create_order(")
    assert gate < create


def test_order_idempotency_race_uses_savepoint_not_outer_rollback():
    from pathlib import Path

    from app.services import order_create_service

    source = inspect.getsource(order_create_service.OrderCreateService.create_order)
    race = source.index("except IntegrityError:")
    recovery = source[race:source.index("subtotal, packing_fee", race)]
    assert "db.begin_nested()" in source
    assert "await db.rollback()" not in recovery

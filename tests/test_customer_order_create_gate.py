"""Customer checkout must serialize order creation with customer merge."""
import inspect

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


def test_pos_cash_settlement_acquires_customer_gate_before_order_lock():
    from app.services.payment_service import PaymentService

    source = inspect.getsource(PaymentService.settle_cash_payment)
    owner_peek = source.index("select(Order.customer_id)")
    gate = source.index("await lock_customer_write_gates")
    order_lock = source.index(".with_for_update()")
    owner_recheck = source.index("locked_owner_id != observed_owner_id")
    assert owner_peek < gate < order_lock < owner_recheck

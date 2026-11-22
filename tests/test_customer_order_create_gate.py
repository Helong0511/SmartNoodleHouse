"""Customer checkout must serialize order creation with customer merge."""
import inspect

from app.api import orders


def test_customer_order_creation_acquires_lifecycle_gate_before_writing_order():
    source = inspect.getsource(orders.create_order)
    gate = source.index("await lock_active_customer_for_write(db, int(customer.id))")
    create = source.index("await OrderCreateService.create_order(")
    assert gate < create

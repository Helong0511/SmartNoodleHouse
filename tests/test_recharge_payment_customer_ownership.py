"""Regression contracts for recharge/payment ownership across customer merges."""
import inspect

from app.api import wallet
from app.services.payment_service import PaymentService


def test_recharge_payment_ownership_comes_from_locked_recharge_order():
    source = inspect.getsource(PaymentService.create_recharge_payment)
    assert "payment.customer_id = int(recharge.customer_id)" in source
    assert "customer_id=int(recharge.customer_id)" in source
    assert "payment.customer_id = customer_id" not in source
    assert "customer_id=customer_id or recharge.customer_id" not in source


def test_pending_recharge_payment_is_locked_after_recharge_order():
    source = inspect.getsource(PaymentService.create_recharge_payment)
    recharge_lock = source.index("select(RechargeOrder)")
    pending_query = source.index("Payment.recharge_order_id == recharge.id")
    pending_lock = source.index(".with_for_update()", pending_query)
    assert recharge_lock < pending_query < pending_lock


def test_recharge_response_does_not_disclose_merged_target_wallet():
    source = inspect.getsource(wallet.create_recharge)
    owner_check = source.index("int(ro.customer_id) == int(customer.id)")
    wallet_snapshot = source.index("BalanceService.snapshot(cust)", owner_check)
    assert owner_check < wallet_snapshot


def test_recharge_payment_acquires_customer_gate_before_parent_row_lock():
    source = inspect.getsource(PaymentService.create_recharge_payment)
    gate = source.index("await lock_customer_write_gates(db, [int(observed_owner_id)])")
    locked_recharge = source.index(".with_for_update()", source.index("locked = ("))
    assert gate < locked_recharge


def test_recharge_creation_acquires_customer_gate_before_creating_recharge_order():
    source = inspect.getsource(wallet.create_recharge)
    gate = source.index("lock_active_customer_for_write")
    package_lookup = source.index("pkg = await db.get")
    assert gate < package_lookup

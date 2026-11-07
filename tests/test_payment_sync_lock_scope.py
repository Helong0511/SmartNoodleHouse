"""Keep third-party payment queries outside database row-lock scopes."""
import inspect

from app.services.payment_service import PaymentService


def test_order_level_sync_does_not_lock_order_before_provider_query():
    source = inspect.getsource(PaymentService.sync_order_pending_payment)
    assert "with_for_update" not in source, (
        "sync_order_pending_payment must not hold the Order row lock while it calls "
        "sync_payment_status (which performs provider HTTP queries)"
    )


def test_payment_sync_queries_provider_before_acquiring_order_payment_locks():
    source = inspect.getsource(PaymentService.sync_payment_status)
    query_pos = source.index("await cls._query_channel_trade(peek)")
    lock_pos = source.index("await cls._lock_order_then_payment(db, pid)")
    assert query_pos < lock_pos, (
        "provider HTTP query must finish before Order→Payment row locks are acquired"
    )


def test_recharge_sync_does_not_lock_recharge_order_before_provider_query():
    source = inspect.getsource(PaymentService.sync_recharge_payment)
    assert "with_for_update" not in source, (
        "sync_recharge_payment must not hold the RechargeOrder row lock while "
        "sync_payment_status performs provider HTTP queries"
    )


def test_recharge_settlement_locks_recharge_before_payment():
    source = inspect.getsource(PaymentService._settle_recharge_payment)
    recharge_lock = source.index("select(RechargeOrder)")
    payment_lock = source.index("select(Payment)")
    assert recharge_lock < payment_lock, (
        "recharge settlement must use RechargeOrder→Payment lock order "
        "to match the shared callback lock helper and avoid deadlocks"
    )


def test_refund_creation_locks_parent_aggregate_before_idempotency_refund_row():
    source = inspect.getsource(PaymentService.refund)
    parent_lock = source.index("_lock_order_then_payment(db, int(payment.id))")
    refund_row_lock = source.index("PaymentRefund).where(PaymentRefund.idempotency_key")
    assert parent_lock < refund_row_lock, (
        "refund creation/replay must acquire Order→Payment before PaymentRefund"
    )


def test_refund_callback_locks_parent_aggregate_before_refund_row():
    source = inspect.getsource(PaymentService._apply_refund_callback)
    provider_query = source.find("trade_refund_query")
    parent_lock = source.index("_lock_order_then_payment(db, int(row.payment_id))")
    refund_row_lock = source.index(".with_for_update()", parent_lock)
    assert provider_query < parent_lock < refund_row_lock, (
        "refund callback must query provider lock-free, then acquire Order→Payment→Refund"
    )


def test_refund_sync_locks_parent_aggregate_before_refund_row():
    source = inspect.getsource(PaymentService.sync_refund_status)
    provider_query = source.index("await cls._query_channel_refund(")
    parent_lock = source.index("_lock_order_then_payment(db, int(payment.id))")
    refund_row_lock = source.index(".with_for_update()", parent_lock)
    assert provider_query < parent_lock < refund_row_lock, (
        "refund sync must query provider lock-free, then acquire Order→Payment→Refund"
    )


def test_refund_poller_locks_parent_aggregate_before_refund_row():
    source = inspect.getsource(PaymentService.poll_pending_refunds)
    parent_lock = source.index("_lock_order_then_payment(", source.index("result = await cls._query_channel_refund"))
    refund_row_lock = source.index(".with_for_update()", parent_lock)
    assert parent_lock < refund_row_lock, (
        "refund poller must acquire Order→Payment before PaymentRefund"
    )


def test_refund_exception_handlers_lock_parent_aggregate_before_refund_row():
    source = inspect.getsource(PaymentService.refund)
    exception_start = source.index("except AppException as e:")
    generic_exception_start = source.index("except Exception as e:", exception_start)
    success_path_start = source.index("# Reacquire parent locks before the refund row", generic_exception_start)
    app_exception = source[exception_start:generic_exception_start]
    generic_exception = source[generic_exception_start:success_path_start]
    for name, block in (("AppException", app_exception), ("Exception", generic_exception)):
        parent_lock = block.index("_lock_order_then_payment(db, int(payment_id))")
        refund_lock = block.index("PaymentRefund")
        assert parent_lock < refund_lock, f"{name} handler must lock Order→Payment before PaymentRefund"

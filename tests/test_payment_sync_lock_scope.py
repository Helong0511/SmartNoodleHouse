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

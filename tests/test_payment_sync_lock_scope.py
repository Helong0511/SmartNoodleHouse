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



def test_alipay_refund_callback_queries_provider_before_opening_db_write_transaction():
    import inspect

    from app.services.payment_service import PaymentService

    handler_source = inspect.getsource(PaymentService.handle_callback)
    provider_query_pos = handler_source.index("await alipay_client.trade_refund_query")
    receipt_insert_pos = handler_source.index("db.add(")
    assert provider_query_pos < receipt_insert_pos, (
        "Alipay refund.query must finish before PaymentCallbackReceipt insert starts "
        "the callback transaction"
    )

    apply_source = inspect.getsource(PaymentService._apply_refund_callback)
    assert "await alipay_client.trade_refund_query" not in apply_source, (
        "_apply_refund_callback must consume the pre-fetched provider result, "
        "not issue HTTP while the receipt transaction is open"
    )

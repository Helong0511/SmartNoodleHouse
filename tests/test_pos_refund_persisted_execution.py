"""POS 退款最终状态必须以数据库累计执行金额为准。"""
from decimal import Decimal
from types import SimpleNamespace

from app.services.pos_refund_service import PosRefundService


def test_sync_success_uses_persisted_executed_amount_not_stale_local_snapshot():
    # PaymentService._finalize_refund 已将渠道同步 SUCCESS 累计到 POS 申请。
    row = SimpleNamespace(
        executed_amount=Decimal("25.00"),
        status="SUCCESS",
        fail_reason=None,
    )

    executed = PosRefundService._finalize_persisted_execution_state(
        row, Decimal("25.00")
    )

    assert executed == Decimal("25.00")
    assert row.status == "SUCCESS"
    assert row.fail_reason is None


def test_partial_execution_remains_failed_and_reports_persisted_amount():
    row = SimpleNamespace(
        executed_amount=Decimal("8.50"),
        status="PROCESSING",
        fail_reason=None,
    )

    executed = PosRefundService._finalize_persisted_execution_state(
        row, Decimal("25.00")
    )

    assert executed == Decimal("8.50")
    assert row.status == "FAILED"
    assert "8.50" in row.fail_reason


def test_reconciliation_required_is_never_downgraded():
    row = SimpleNamespace(
        executed_amount=Decimal("26.00"),
        status="RECONCILIATION_REQUIRED",
        fail_reason="实际退款超额",
    )

    PosRefundService._finalize_persisted_execution_state(row, Decimal("25.00"))

    assert row.status == "RECONCILIATION_REQUIRED"
    assert "超额" in row.fail_reason

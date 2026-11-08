"""现金收款不得与未确认的第三方支付并行。"""
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppException


@pytest.mark.asyncio
async def test_cash_payment_rejects_pending_wechat_or_alipay_flow():
    from app.services.payment_service import PaymentService

    order = SimpleNamespace(
        id=10,
        status="PENDING_PAYMENT",
        payment_status="PENDING",
    )
    pending = SimpleNamespace(id=99, provider="WECHAT", status="PENDING")

    class ScalarRows:
        def all(self):
            return [pending]

    class Result:
        def scalar_one_or_none(self):
            return order

        def scalars(self):
            return ScalarRows()

    class FakeDB:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(str(statement).lower())
            return Result()

    db = FakeDB()
    with pytest.raises(AppException) as exc:
        await PaymentService.settle_cash_payment(db, order)

    assert exc.value.code == 40908
    assert exc.value.status_code == 409
    assert len(db.statements) == 2
    assert "from orders" in db.statements[0] and "for update" in db.statements[0]
    assert "from payments" in db.statements[1]
    assert "payments.status" in db.statements[1]
    assert "payments.provider" in db.statements[1]

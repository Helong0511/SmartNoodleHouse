"""POS customer binding must use current locked reads in merge lock order."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.exceptions import AppException
from app.models import Customer, Order
from app.api import orders_pos


@pytest.mark.asyncio
async def test_pos_bind_rejects_customer_disabled_by_merge_after_request_started(monkeypatch):
    order = SimpleNamespace(
        id=91,
        store_id=7,
        channel="POS",
        status="PENDING_PAYMENT",
        payment_status="UNPAID",
        customer_id=None,
        order_no="POS-LOCK-91",
        customer_coupon_id=None,
        customer_voucher_id=None,
        total_amount="10.00",
        discount_amount="0.00",
        coupon_discount="0.00",
        voucher_discount="0.00",
    )
    disabled_customer = SimpleNamespace(
        id=33, status=0, account_status="DISABLED"
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[Result(order), Result(disabled_customer)])
    )
    monkeypatch.setattr(orders_pos, "assert_order_store_access", lambda *_: None)

    with pytest.raises(AppException) as exc:
        await orders_pos.bind_pos_customer(
            order_id=91,
            data=SimpleNamespace(customer_id=33),
            request=SimpleNamespace(),
            db=db,
            user=SimpleNamespace(id=5),
        )

    assert exc.value.status_code == 404
    assert db.execute.await_count == 2
    statements = [call.args[0] for call in db.execute.await_args_list]
    assert statements[0].column_descriptions[0]["entity"] is Order
    assert statements[1].column_descriptions[0]["entity"] is Customer
    assert all(stmt._for_update_arg is not None for stmt in statements)

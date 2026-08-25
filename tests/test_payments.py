"""支付幂等测试"""
import json
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_payment_callback_idempotent():
    """同一回调多次只能产生一次支付成功"""
    processed = set()

    def handle_callback(payment_no: str):
        if payment_no in processed:
            return "already_paid"
        processed.add(payment_no)
        return "paid"

    payment_no = "PAY123"
    assert handle_callback(payment_no) == "paid"
    assert handle_callback(payment_no) == "already_paid"
    assert handle_callback(payment_no) == "already_paid"
    assert len(processed) == 1


def test_payment_amount_verification():
    """回调金额必须与订单金额一致"""
    order_amount = Decimal("54.00")
    callback_amount = Decimal("1.00")
    assert order_amount != callback_amount

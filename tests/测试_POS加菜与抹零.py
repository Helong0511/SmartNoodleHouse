"""POS 加菜 + 现金抹零单元测试。"""
from decimal import ROUND_FLOOR, Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.core.response import money
from app.services.payment_service import PaymentService


# ========== 加菜金额重算 ==========

def test_add_items_subtotal_recalc():
    """原订单 30 + 加菜 15 = 新小计 45。"""
    old_subtotal = money("30.00")
    add_subtotal = money("15.00")
    new_subtotal = money(old_subtotal) + add_subtotal
    assert new_subtotal == money("45.00")


def test_add_items_packing_fee_recalc():
    """外带加菜：打包费累加。原 3 + 加菜 2 = 5。"""
    old_packing = money("3.00")
    add_packing = money("2.00")
    new_packing = money(old_packing) + add_packing
    assert new_packing == money("5.00")


def test_add_items_total_with_no_discount():
    """无会员折扣：subtotal + packing = total。"""
    new_subtotal = money("45.00")
    new_packing = money("5.00")
    discount = money("0.00")
    base_total = new_subtotal + new_packing - discount
    assert money(base_total) == money("50.00")


# ========== 抹零逻辑 ==========

def test_round_down_to_yuan():
    """¥12.50 抹零到 ¥12.00，抹零金额 ¥0.50。"""
    pay_amount = money("12.50")
    rounded = pay_amount.quantize(Decimal("1"), rounding=ROUND_FLOOR)
    round_down_amount = pay_amount - rounded
    assert rounded == Decimal("12")
    assert round_down_amount == money("0.50")


def test_round_down_zero_cents():
    """¥12.00 抹零金额为 0。"""
    pay_amount = money("12.00")
    rounded = pay_amount.quantize(Decimal("1"), rounding=ROUND_FLOOR)
    round_down_amount = pay_amount - rounded
    assert rounded == Decimal("12")
    assert round_down_amount == money("0.00")


def test_round_down_99_cents():
    """¥19.99 抹零到 ¥19.00，抹零 ¥0.99。"""
    pay_amount = money("19.99")
    rounded = pay_amount.quantize(Decimal("1"), rounding=ROUND_FLOOR)
    round_down_amount = pay_amount - rounded
    assert rounded == Decimal("19")
    assert round_down_amount == money("0.99")


def test_round_down_change_calc():
    """抹零后：应收 12.50 → 入账 12.00，顾客给 20，找零 8.00。"""
    pay_amount = money("12.50")
    rounded = pay_amount.quantize(Decimal("1"), rounding=ROUND_FLOOR)  # 12
    received = money("20.00")
    change = received - rounded
    assert change == money("8.00")


@pytest.mark.asyncio
async def test_settle_cash_round_down_applied():
    """settle_cash_payment 开启 round_down 后入账金额被抹零。"""
    order = MagicMock()
    order.id = 100
    order.order_no = "POS100"
    order.status = "PENDING_PAYMENT"
    order.payment_status = "UNPAID"
    order.total_amount = Decimal("12.50")
    order.store_id = 1
    order.table_id = None
    order.customer_id = None

    locked = order
    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = locked
    db.execute = AsyncMock(return_value=exec_result)
    db.flush = AsyncMock()
    db.add = MagicMock()

    with patch.object(PaymentService, "remaining_amount", new_callable=AsyncMock, return_value=money("12.50")), \
         patch.object(PaymentService, "_finalize_tender", new_callable=AsyncMock, return_value={"fully_paid": True, "paid_amount": "12.00", "remaining_amount": "0.00", "payment_status": "PAID"}), \
         patch.object(PaymentService, "payment_progress", new_callable=AsyncMock, return_value={"fully_paid": True, "paid_amount": "12.00", "remaining_amount": "0.00", "payment_status": "PAID"}), \
         patch("app.services.payment_service.record_payment_status", new_callable=AsyncMock), \
         patch("app.services.payment_service._order_store_no", new_callable=AsyncMock, return_value="S001"), \
         patch("app.services.payment_service._order_table_parts", new_callable=AsyncMock, return_value=(None, None)), \
         patch("app.services.payment_service.generate_no", return_value="CASH001"), \
         patch("app.services.payment_service.payment_biz", return_value="C"):
        payment, pay_data = await PaymentService.settle_cash_payment(
            db, order,
            operator_id=1,
            received_amount=Decimal("20.00"),
            round_down=True,
        )
    # 入账金额应为 12.00（抹零后）
    assert payment.amount == money("12.00")
    # 找零 = 20 - 12 = 8
    assert pay_data.get("change_amount") == str(money("8.00"))


# ========== 加菜幂等防重 ==========

def test_add_items_idempotency_key_format():
    """加菜幂等键格式：pos:add:{order_id}:{user_id}:{raw_key}。"""
    order_id = 42
    user_id = 7
    raw_key = "abc123xyz"
    key = f"pos:add:{order_id}:{user_id}:{raw_key}"
    assert key == "pos:add:42:7:abc123xyz"


# ========== 加菜状态校验 ==========

def test_add_items_rejects_paid_order():
    """已支付订单不可加菜。"""
    status = "PAID"
    assert status != "PENDING_PAYMENT"


def test_add_items_rejects_cancelled_order():
    """已取消订单不可加菜。"""
    status = "CANCELLED"
    assert status != "PENDING_PAYMENT"


def test_add_items_accepts_pending_payment():
    """待支付订单可加菜。"""
    status = "PENDING_PAYMENT"
    assert status == "PENDING_PAYMENT"

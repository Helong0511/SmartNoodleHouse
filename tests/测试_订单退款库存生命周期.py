"""退款与库存生命周期回归：退款不应把已经制作/出餐的餐品重新计入库存。"""
import pytest

from app.services.order_lifecycle import _default_restore_inventory


@pytest.mark.parametrize(
    "old_status,target_status,expected",
    [
        ("PENDING_PAYMENT", "CANCELLED", True),
        ("PAID", "REFUNDED", True),
        ("CONFIRMED", "REFUNDED", True),
        ("PREPARING", "REFUNDED", False),
        ("COOKING", "REFUNDED", False),
        ("READY", "REFUNDED", False),
        ("COMPLETED", "REFUNDED", False),
        ("REFUND_PENDING", "REFUNDED", False),
        ("REFUNDED", "REFUNDED", False),
        ("PAID", "COMPLETED", False),
    ],
)
def test_default_restore_inventory_respects_food_preparation_lifecycle(
    old_status, target_status, expected
):
    assert _default_restore_inventory(old_status, target_status) is expected

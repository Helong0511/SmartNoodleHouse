"""单品数量上限：宴会/团餐可超过 99。"""
from pydantic import ValidationError

from app.schemas.order import MAX_ORDER_ITEM_QUANTITY, OrderItemCreate


def test_order_item_quantity_allows_over_100():
    item = OrderItemCreate(product_id=1, quantity=109)
    assert item.quantity == 109


def test_order_item_quantity_rejects_over_max():
    try:
        OrderItemCreate(product_id=1, quantity=MAX_ORDER_ITEM_QUANTITY + 1)
        assert False, "should raise"
    except ValidationError as e:
        assert any("less_than_equal" in str(err.get("type", "")) or err.get("type") == "less_than_equal" for err in e.errors())


def test_max_order_item_quantity_is_999():
    assert MAX_ORDER_ITEM_QUANTITY == 999
    OrderItemCreate(product_id=1, quantity=999)

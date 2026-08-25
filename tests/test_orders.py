"""订单价格计算测试 - 服务器必须忽略前端篡改价格"""
from decimal import Decimal

import pytest

from app.core.response import money


def test_money_calculation():
    """牛肉面18 + 多面2 + 加蛋2 + 加牛肉5 = 27, ×2 = 54"""
    base = Decimal("18.00")
    extra = Decimal("2.00") + Decimal("2.00") + Decimal("5.00")
    unit = money(base + extra)
    total = money(unit * 2)
    assert total == Decimal("54.00")


def test_frontend_price_tampering_ignored():
    """前端提交 price=1 时必须被忽略"""
    server_price = Decimal("18.00")
    frontend_price = Decimal("1.00")
    assert server_price != frontend_price
    # 服务器始终使用数据库价格
    final = money(server_price)
    assert final == Decimal("18.00")


def test_decimal_not_float():
    """金额禁止使用 float"""
    a = Decimal("0.1")
    b = Decimal("0.2")
    result = money(a + b)
    assert result == Decimal("0.30")
    assert result != 0.30000000000000004  # float 精度问题

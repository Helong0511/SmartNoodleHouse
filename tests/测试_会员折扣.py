"""会员折扣计价（商品小计打折，打包费不参与）。"""
from decimal import Decimal

from app.core.response import money
from app.services.customer_service import CustomerService


def test_no_discount_when_rate_is_one():
    discount, total = CustomerService.calc_member_discount(
        Decimal("100.00"), Decimal("2.00"), Decimal("1.0000")
    )
    assert discount == Decimal("0.00")
    assert total == Decimal("102.00")


def test_silver_card_98_percent():
    discount, total = CustomerService.calc_member_discount(
        Decimal("100.00"), Decimal("0.00"), Decimal("0.9800")
    )
    assert discount == Decimal("2.00")
    assert total == Decimal("98.00")


def test_gold_card_with_packing_fee():
    discount, total = CustomerService.calc_member_discount(
        Decimal("50.00"), Decimal("3.00"), Decimal("0.9500")
    )
    assert discount == Decimal("2.50")
    assert total == money(Decimal("50.00") - Decimal("2.50") + Decimal("3.00"))


def test_invalid_rate_treated_as_no_discount():
    discount, total = CustomerService.calc_member_discount(
        Decimal("10.00"), Decimal("0.00"), Decimal("1.50")
    )
    assert discount == Decimal("0.00")
    assert total == Decimal("10.00")


def test_special_price_excluded_from_member_discount():
    discount, total = CustomerService.calc_member_discount(
        Decimal("100.00"),
        Decimal("0.00"),
        Decimal("0.9800"),
        discountable_subtotal=Decimal("60.00"),
    )
    assert discount == Decimal("1.20")
    assert total == Decimal("98.80")


def test_all_special_price_no_member_discount():
    discount, total = CustomerService.calc_member_discount(
        Decimal("50.00"),
        Decimal("2.00"),
        Decimal("0.9500"),
        discountable_subtotal=Decimal("0.00"),
    )
    assert discount == Decimal("0.00")
    assert total == Decimal("52.00")


def test_coupon_eligible_excludes_special_price():
    eligible = CustomerService.calc_coupon_eligible_subtotal(
        Decimal("100.00"),
        Decimal("60.00"),
        Decimal("1.20"),
    )
    assert eligible == Decimal("58.80")


def test_coupon_eligible_all_normal():
    eligible = CustomerService.calc_coupon_eligible_subtotal(
        Decimal("100.00"),
        Decimal("100.00"),
        Decimal("2.00"),
    )
    assert eligible == Decimal("98.00")


def test_voucher_eligible_includes_packing_only_on_non_special():
    eligible = CustomerService.calc_voucher_eligible_payable(
        Decimal("58.80"),
        Decimal("5.00"),
        Decimal("2.00"),
        need_pack=True,
    )
    assert eligible == Decimal("55.80")

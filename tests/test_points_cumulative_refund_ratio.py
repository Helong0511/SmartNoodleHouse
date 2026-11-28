from decimal import Decimal

from app.services.customer_service import CustomerService


def test_birthday_multiplier_points_are_fully_reversed_on_full_refund() -> None:
    assert CustomerService.points_for_cumulative_refunds(
        200, Decimal("100.00"), Decimal("100.00")
    ) == 200


def test_partial_refund_reverses_same_ratio_of_actual_earned_points() -> None:
    assert CustomerService.points_for_cumulative_refunds(
        200, Decimal("25.00"), Decimal("100.00")
    ) == 50


def test_cumulative_rounding_prevents_split_refund_point_drift() -> None:
    # Two 0.40 refunds each round to zero in isolation, but together reverse 1 point.
    assert CustomerService.points_for_cumulative_refunds(
        1, Decimal("0.40"), Decimal("1.00")
    ) == 0
    assert CustomerService.points_for_cumulative_refunds(
        1, Decimal("0.80"), Decimal("1.00")
    ) == 1


def test_cumulative_refund_clawback_is_capped_at_earned_points() -> None:
    assert CustomerService.points_for_cumulative_refunds(
        37, Decimal("150.00"), Decimal("100.00")
    ) == 37


def test_zero_or_invalid_order_amount_never_creates_point_clawback() -> None:
    assert CustomerService.points_for_cumulative_refunds(
        200, Decimal("10.00"), Decimal("0.00")
    ) == 0

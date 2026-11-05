from decimal import Decimal

import pytest

from app.services.payment_reconcile_service import parse_channel_bill_csv


def test_channel_bill_parser_keeps_trade_amount_and_payment_number():
    rows = parse_channel_bill_csv(
        "WECHAT",
        "trade_no,amount,payment_no\nWX-001,12.30,PAY-001\n",
    )
    assert len(rows) == 1
    assert rows[0]["channel"] == "WECHAT"
    assert rows[0]["channel_trade_no"] == "WX-001"
    assert rows[0]["channel_amount"] == Decimal("12.30")
    assert rows[0]["local_payment_no"] == "PAY-001"


def test_channel_bill_parser_rejects_duplicate_trade_numbers_instead_of_overwriting():
    with pytest.raises(ValueError, match="重复交易号"):
        parse_channel_bill_csv(
            "ALIPAY",
            "trade_no,amount\nALI-001,12.30\nALI-001,12.30\n",
        )


def test_channel_bill_parser_rejects_negative_amounts():
    with pytest.raises(ValueError, match="不能为负数"):
        parse_channel_bill_csv(
            "WECHAT",
            "trade_no,amount\nWX-NEG,-1.00\n",
        )

@pytest.mark.parametrize(
    "bill, message",
    [
        ("wrong,columns\\nWX-1,1.00\\n", "缺少交易号或金额列"),
        ("trade_no,amount\\n,1.00\\n", "缺少交易号"),
        ("trade_no,amount\\nWX-1,NaN\\n", "金额无效"),
        ("trade_no,amount\\nWX-1,Infinity\\n", "金额无效"),
        ("trade_no,amount\\nWX-1,1.001\\n", "最多两位小数"),
    ],
)
def test_channel_bill_parser_rejects_malformed_or_non_finite_amount_rows(bill, message):
    with pytest.raises(ValueError, match=message):
        parse_channel_bill_csv("WECHAT", bill)


def test_channel_bill_parser_rejects_missing_header():
    with pytest.raises(ValueError, match="缺少 CSV 表头"):
        parse_channel_bill_csv("WECHAT", "")


def test_channel_bill_parser_allows_empty_bill_with_valid_headers():
    assert parse_channel_bill_csv("WECHAT", "trade_no,amount,payment_no\\n") == []


"""业务单号生成：业务+门店编号+桌位+时间+4位随机。"""
from __future__ import annotations

import re

import pytest

from app.core.biz_no import (
    BIZ_OD,
    BIZ_OT,
    BIZ_PA,
    BIZ_PW,
    BIZ_RA,
    BIZ_RW,
    TABLE_DELIVERY,
    TABLE_TAKEAWAY,
    generate_no,
    parse_biz_no,
    payment_biz,
    refund_biz,
    resolve_table_parts,
    store_code,
    table_code,
)


def test_store_code_from_store_no():
    assert store_code("SH01") == "SH01"
    assert store_code("sh01") == "SH01"
    assert store_code(None) == "0000"
    assert store_code("") == "0000"
    assert store_code("SH") == "SH00"
    assert store_code("SNH001") == "SNH0"


def test_table_code_fixed_width():
    assert table_code("A01") == "A01"
    assert table_code("12") == "12X"
    assert table_code("桌-01") == "01X"
    assert table_code(None) == "000"
    assert table_code("", placeholder=TABLE_DELIVERY) == "DLV"
    assert table_code("ABCD") == "ABC"


def test_generate_no_shape_and_parse():
    no = generate_no(BIZ_OT, "SH01", "A01")
    assert len(no) == 27
    assert no.startswith("OTSH01A01")
    parsed = parse_biz_no(no)
    assert parsed is not None
    assert parsed["biz"] == "OT"
    assert parsed["store"] == "SH01"
    assert parsed["table"] == "A01"
    assert re.fullmatch(r"\d{14}", parsed["datetime"])
    assert re.fullmatch(r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}", parsed["rand"])


def test_delivery_and_payment_codes():
    od = generate_no(BIZ_OD, "SH02", None, table_placeholder=TABLE_DELIVERY)
    assert od.startswith("ODSH02DLV")
    assert len(od) == 27

    assert payment_biz("WECHAT") == BIZ_PW
    assert payment_biz("ALIPAY") == BIZ_PA
    assert refund_biz("WECHAT") == BIZ_RW
    assert refund_biz("ALIPAY") == BIZ_RA

    pw = generate_no(BIZ_PW, "SH01", "A01")
    assert pw.startswith("PWSH01A01")
    assert parse_biz_no("ORD20260827134405A1B2C3") is None  # 旧格式


def test_resolve_table_parts():
    assert resolve_table_parts("A01")[0] == "A01"
    assert resolve_table_parts(None, channel="MEITUAN")[1] == TABLE_DELIVERY
    assert resolve_table_parts(None, dining_type="TAKEAWAY")[1] == TABLE_TAKEAWAY


def test_invalid_biz():
    with pytest.raises(ValueError):
        generate_no("ORD", "SH01", "A01")

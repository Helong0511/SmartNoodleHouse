"""规格编码：0000-/门店号- 前缀规则 + 拼音建议。"""
import pytest

from app.services.option_code import (
    format_category_no,
    format_group_code,
    format_option_code,
    format_product_no,
    group_logical_key,
    name_to_logical_code,
    normalize_store_prefix,
    option_logical_key,
    suggest_code,
    validate_prefixed_code,
)


def test_hq_and_store_group_codes():
    assert format_group_code("0000", "Liang") == "0000-Liang"
    assert format_group_code(None, "Liang") == "0000-Liang"
    assert format_group_code("SH01", "Liang") == "SH01-Liang"
    assert format_group_code("sh01", "0000-Liang") == "SH01-Liang"
    assert group_logical_key("SH01-Liang") == "Liang"


def test_option_codes():
    assert format_option_code("0000", "0000-Liang", "XiaoWan") == "0000-Liang-XiaoWan"
    assert format_option_code("SH01", "SH01-Liang", "XiaoWan") == "SH01-Liang-XiaoWan"
    assert format_option_code("SH01", "0000-Liang", "0000-Liang-XiaoWan") == "SH01-Liang-XiaoWan"
    assert option_logical_key("0000-Liang-XiaoWan", "0000-Liang") == "Liang-XiaoWan"


def test_normalize_store_prefix():
    assert normalize_store_prefix(None) == "0000"
    assert normalize_store_prefix("sh01") == "SH01"


def test_name_to_logical_pinyin():
    assert name_to_logical_code("牛肉面") == "NiuRouMian"
    assert name_to_logical_code("绵阳米粉") == "MianYangMiFen"
    assert name_to_logical_code("小碗") == "XiaoWan"


def test_category_and_product_nos():
    assert format_category_no("SH01", "Mian") == "SH01-Mian"
    assert format_product_no("0000", "NiuRouMian", category_logical="Mian") == "0000-Mian-NiuRouMian"
    assert format_product_no("SH01", "NiuRouMian", category_logical="SH01-Mian") == "SH01-Mian-NiuRouMian"
    assert format_product_no("B01", "A01-Mian-N001", category_logical="Mian") == "B01-Mian-N001"


def test_validate_prefixed_code():
    assert (
        validate_prefixed_code("0000-Mian-NiuRouMian", max_len=64, expect_prefix="0000")
        == "0000-Mian-NiuRouMian"
    )
    with pytest.raises(ValueError):
        validate_prefixed_code("NiuRouMian", max_len=64)
    with pytest.raises(ValueError):
        validate_prefixed_code("SH01-牛肉", max_len=64)
    with pytest.raises(ValueError):
        validate_prefixed_code("B01-X", max_len=64, expect_prefix="SH01")


def test_suggest_code():
    s = suggest_code(name="牛肉面", store_prefix="0000", kind="product", category_logical="Mian")
    assert s["logical"] == "NiuRouMian"
    assert s["code"] == "0000-Mian-NiuRouMian"
    s2 = suggest_code(name="牛肉面", store_prefix="SH01", kind="product", category_logical="面")
    assert s2["code"] == "SH01-Mian-NiuRouMian"
    c = suggest_code(name="小吃", store_prefix="SH01", kind="category")
    assert c["code"] == "SH01-XiaoChi"
    g = suggest_code(name="量", store_prefix="0000", kind="group")
    assert g["code"] == "0000-Liang"
    o = suggest_code(name="小碗", store_prefix="SH01", kind="option", group_code="SH01-Liang")
    assert o["code"] == "SH01-Liang-XiaoWan"

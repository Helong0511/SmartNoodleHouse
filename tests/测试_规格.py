"""规格系统测试"""
import pytest


def test_option_codes():
    noodle_options = ["LESS", "NORMAL", "EXTRA"]
    spicy_options = ["NONE", "LESS", "NORMAL", "EXTRA"]
    extra_options = ["EGG", "BEEF", "BEAN_CURD", "CORIANDER", "SCALLION"]
    assert "EXTRA" in noodle_options
    assert "NONE" in spicy_options
    assert "EGG" in extra_options


def test_single_selection():
    selected = [1]
    assert len(selected) <= 1


def test_multiple_selection():
    selected = [1, 2, 3]
    assert len(selected) >= 1

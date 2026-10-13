"""商品限时/生日展示规则（含公历/农历）。"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.utils.birthday import CAL_LUNAR, CAL_SOLAR, lunar_md
from app.utils.product_visibility import (
    VIS_ANNUAL_MD,
    VIS_BIRTHDAY,
    VIS_DATE_RANGE,
    filter_visible_product_dicts,
    is_product_visible,
)


def _p(**kw):
    return SimpleNamespace(
        visibility_rule=kw.get("visibility_rule", "ALWAYS"),
        visibility_calendar=kw.get("visibility_calendar", CAL_SOLAR),
        visible_from=kw.get("visible_from"),
        visible_to=kw.get("visible_to"),
        name=kw.get("name", "x"),
    )


def test_always_visible():
    assert is_product_visible(_p(), today=date(2026, 9, 10))


def test_date_range():
    p = _p(
        visibility_rule=VIS_DATE_RANGE,
        visible_from=date(2026, 9, 8),
        visible_to=date(2026, 9, 12),
    )
    assert is_product_visible(p, today=date(2026, 9, 10))
    assert not is_product_visible(p, today=date(2026, 9, 7))
    assert not is_product_visible(p, today=date(2026, 9, 13))


def test_annual_md_teachers_day_solar():
    p = _p(
        visibility_rule=VIS_ANNUAL_MD,
        visibility_calendar=CAL_SOLAR,
        visible_from=date(2000, 9, 10),
        visible_to=date(2000, 9, 10),
    )
    assert is_product_visible(p, today=date(2026, 9, 10))
    assert is_product_visible(p, today=date(2030, 9, 10))
    assert not is_product_visible(p, today=date(2026, 9, 11))


def test_annual_md_wrap_year_solar():
    p = _p(
        visibility_rule=VIS_ANNUAL_MD,
        visibility_calendar=CAL_SOLAR,
        visible_from=date(2000, 12, 20),
        visible_to=date(2000, 1, 5),
    )
    assert is_product_visible(p, today=date(2026, 12, 25))
    assert is_product_visible(p, today=date(2027, 1, 3))
    assert not is_product_visible(p, today=date(2026, 6, 1))


def test_annual_md_lunar_spring_festival():
    """农历正月初一：用某年春节的公历日作锚点，其它年同农历月日也可见。"""
    # 2025-01-29 = 农历乙巳年正月初一
    anchor = date(2025, 1, 29)
    am, ad, _ = lunar_md(anchor)
    assert (am, ad) == (1, 1)
    p = _p(
        visibility_rule=VIS_ANNUAL_MD,
        visibility_calendar=CAL_LUNAR,
        visible_from=anchor,
        visible_to=anchor,
    )
    assert is_product_visible(p, today=date(2025, 1, 29))
    # 2026 春节公历 2026-02-17
    assert is_product_visible(p, today=date(2026, 2, 17))
    assert not is_product_visible(p, today=date(2026, 1, 29))


def test_annual_md_lunar_range():
    """农历正月初一～十五。"""
    # 2025 正月初一 / 十五
    p = _p(
        visibility_rule=VIS_ANNUAL_MD,
        visibility_calendar=CAL_LUNAR,
        visible_from=date(2025, 1, 29),
        visible_to=date(2025, 2, 12),
    )
    assert is_product_visible(p, today=date(2025, 2, 5))
    assert is_product_visible(p, today=date(2026, 2, 20))  # 2026 正月初四附近
    assert not is_product_visible(p, today=date(2026, 6, 1))


def test_birthday_today():
    p = _p(visibility_rule=VIS_BIRTHDAY)
    assert not is_product_visible(p, today=date(2026, 5, 1), is_birthday_today=False)
    assert is_product_visible(p, today=date(2026, 5, 1), is_birthday_today=True)


def test_filter_dicts():
    items = [
        {"id": 1, "visibility_rule": "ALWAYS"},
        {
            "id": 2,
            "visibility_rule": "ANNUAL_MD",
            "visibility_calendar": "SOLAR",
            "visible_from": "2000-09-10",
            "visible_to": "2000-09-10",
        },
        {"id": 3, "visibility_rule": "BIRTHDAY_TODAY"},
    ]
    out = filter_visible_product_dicts(
        items, today=date(2026, 9, 10), is_birthday_today=False
    )
    assert [x["id"] for x in out] == [1, 2]
    out2 = filter_visible_product_dicts(
        items, today=date(2026, 9, 10), is_birthday_today=True
    )
    assert [x["id"] for x in out2] == [1, 2, 3]


def test_date_range_missing_bounds_hidden():
    p = _p(visibility_rule=VIS_DATE_RANGE, visible_from=None, visible_to=None)
    assert not is_product_visible(p, today=date(2026, 9, 10))


def test_date_range_open_end():
    p = _p(
        visibility_rule=VIS_DATE_RANGE,
        visible_from=date(2026, 9, 1),
        visible_to=None,
    )
    assert is_product_visible(p, today=date(2026, 9, 10))
    assert not is_product_visible(p, today=date(2026, 8, 31))


def test_normalize_unknown_rule_as_always():
    from app.utils.product_visibility import normalize_visibility_rule

    assert normalize_visibility_rule("NOPE") == "ALWAYS"
    assert normalize_visibility_rule(None) == "ALWAYS"
    assert normalize_visibility_rule("birthday_today") == "BIRTHDAY_TODAY"


def test_schema_annual_md_allows_wrap_year_dates():
    """每年月日跨年区间：visible_to 的公历日可早于 visible_from（只比月日）。"""
    from app.schemas.product import ProductCreate

    row = ProductCreate(
        category_id=1,
        product_no="T001",
        name="跨年菜",
        price="18.00",
        visibility_rule="ANNUAL_MD",
        visibility_calendar="SOLAR",
        visible_from=date(2000, 12, 20),
        visible_to=date(2000, 1, 5),
    )
    assert row.visible_to < row.visible_from


def test_schema_date_range_rejects_inverted_bounds():
    from pydantic import ValidationError

    from app.schemas.product import ProductCreate

    try:
        ProductCreate(
            category_id=1,
            product_no="T002",
            name="限时菜",
            price="18.00",
            visibility_rule="DATE_RANGE",
            visible_from=date(2026, 9, 12),
            visible_to=date(2026, 9, 8),
        )
        assert False, "expected ValidationError"
    except ValidationError:
        pass

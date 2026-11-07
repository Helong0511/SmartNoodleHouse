"""生日公历/农历匹配与权益文案。"""
from datetime import date

from app.utils.birthday import (
    CAL_LUNAR,
    CAL_SOLAR,
    MODE_COUPON,
    MODE_OFF,
    MODE_POINTS,
    benefit_summary,
    birthday_for_picker,
    convert_birthday_calendar,
    is_birthday_today,
    is_member_anniversary_today,
    anniversary_md_matches,
    solar_to_lunar_ymd,
    format_birthday_display,
    lunar_ymd_to_solar,
    normalize_calendar,
    resolve_birthday_input,
    resolve_birthday_storage,
)


def test_normalize_calendar_default_solar():
    assert normalize_calendar(None) == CAL_SOLAR
    assert normalize_calendar("lunar") == CAL_LUNAR
    assert normalize_calendar("bogus") == CAL_SOLAR


def test_solar_birthday_match():
    bday = date(1990, 5, 1)
    assert is_birthday_today(bday, CAL_SOLAR, today=date(2026, 5, 1))
    assert not is_birthday_today(bday, CAL_SOLAR, today=date(2026, 5, 2))


def test_lunar_birthday_match_same_lunar_md():
    bday = date(1990, 5, 1)
    assert is_birthday_today(bday, CAL_LUNAR, today=date(1990, 5, 1))
    assert not is_birthday_today(bday, CAL_LUNAR, today=date(2026, 1, 1))


def test_benefit_summary_off():
    s = benefit_summary(mode=MODE_OFF, multiplier=2)
    assert s["enabled"] is False
    assert "未开启" in s["text"]


def test_benefit_summary_points_and_coupon():
    s = benefit_summary(
        mode="points_and_coupon",
        multiplier=2,
        coupon_name="生日满30减5",
        calendar=CAL_LUNAR,
        is_today=True,
    )
    assert s["enabled"] is True
    assert s["points_multiplier"] == 2
    assert s["coupon_name"] == "生日满30减5"
    assert "积分" in s["text"]
    assert "生日满30减5" in s["text"]
    assert s["is_birthday_today"] is True


def test_benefit_summary_coupon_only():
    s = benefit_summary(mode=MODE_COUPON, multiplier=1, coupon_name="生日券")
    assert s["enabled"] is True
    assert "生日券" in s["text"]


def test_convert_keeps_same_solar_anchor():
    # 同日互转：切换历法不改存储的公历锚点（5/1 公历 ↔ 四月初七）
    out = convert_birthday_calendar(date(1990, 5, 1), CAL_SOLAR, CAL_LUNAR)
    assert out == date(1990, 5, 1)
    back = convert_birthday_calendar(out, CAL_LUNAR, CAL_SOLAR)
    assert back == date(1990, 5, 1)


def test_lunar_match_uses_true_lunar_of_anchor():
    # 1990-05-01 农历为四月初七；切到农历后仍在该公历日匹配，不在「五月初一」
    bday = date(1990, 5, 1)
    assert is_birthday_today(bday, CAL_LUNAR, today=date(1990, 5, 1))
    # 1990 农历五月初一 ≈ 1990-05-24，不应因错误重解释而命中
    assert not is_birthday_today(bday, CAL_LUNAR, today=date(1990, 5, 24))


def test_lunar_input_means_lunar_ymd_not_solar_convert():
    """农历模式下用户选 5月1日 = 五月初一，不是把公历 5/1 再转农历。"""
    # 农历 1990-05-01 → 公历约 1990-05-24
    solar = lunar_ymd_to_solar(1990, 5, 1)
    assert solar == date(1990, 5, 24)
    assert resolve_birthday_storage(date(1990, 5, 1), CAL_LUNAR) == solar
    assert resolve_birthday_input("1990-05-01", CAL_LUNAR) == solar
    # 公历模式仍按数字原样存
    assert resolve_birthday_storage(date(1990, 5, 1), CAL_SOLAR) == date(1990, 5, 1)
    # 落库后按农历五月初一匹配
    assert is_birthday_today(solar, CAL_LUNAR, today=date(1990, 5, 24))
    assert not is_birthday_today(solar, CAL_LUNAR, today=date(1990, 5, 1))
    # 控件回填应为农历数字
    assert birthday_for_picker(solar, CAL_LUNAR) == "1990-05-01"
    assert birthday_for_picker(date(1990, 5, 1), CAL_SOLAR) == "1990-05-01"


def test_member_anniversary_requires_no_birthday():
    activated = date(2024, 10, 22)
    today = date(2026, 10, 22)
    assert is_member_anniversary_today(None, activated, today=today)
    assert not is_member_anniversary_today(date(1990, 1, 1), activated, today=today)


def test_member_anniversary_min_one_year():
    activated = date(2026, 10, 22)
    today = date(2026, 10, 22)
    assert not is_member_anniversary_today(None, activated, today=today)
    assert is_member_anniversary_today(None, date(2025, 10, 22), today=today)


def test_anniversary_feb29_non_leap():
    assert anniversary_md_matches(date(2020, 2, 29), date(2026, 2, 28))
    assert not anniversary_md_matches(date(2020, 2, 29), date(2026, 2, 27))
    assert anniversary_md_matches(date(2020, 2, 29), date(2024, 2, 29))


def test_lunar_python_adapter_preserves_leap_month_round_trip():
    # 2023 年闰二月初一对应公历 2023-03-22。
    solar = lunar_ymd_to_solar(2023, 2, 1, leap=True)
    assert solar == date(2023, 3, 22)
    assert solar_to_lunar_ymd(solar) == (2023, 2, 1, True)


def test_lunar_birthday_display_uses_chinese_month_and_day():
    assert format_birthday_display(date(1990, 5, 1), CAL_LUNAR) == (
        "农历 四月初七（对应公历 1990-05-01）"
    )
    assert format_birthday_display(date(2023, 3, 22), CAL_LUNAR) == (
        "农历 闰二月初一（对应公历 2023-03-22）"
    )

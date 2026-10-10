"""生日公历/农历匹配与权益文案。"""
from datetime import date

from app.utils.birthday import (
    CAL_LUNAR,
    CAL_SOLAR,
    MODE_COUPON,
    MODE_OFF,
    MODE_POINTS,
    benefit_summary,
    convert_birthday_calendar,
    is_birthday_today,
    normalize_calendar,
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

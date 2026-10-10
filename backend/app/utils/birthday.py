"""生日日历（公历/农历）与权益日判定。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from zhdate import ZhDate

CAL_SOLAR = "SOLAR"
CAL_LUNAR = "LUNAR"
VALID_CALENDARS = frozenset({CAL_SOLAR, CAL_LUNAR})


def normalize_calendar(raw: str | None) -> str:
    v = (raw or CAL_SOLAR).strip().upper()
    return v if v in VALID_CALENDARS else CAL_SOLAR


def _solar_to_lunar(d: date) -> ZhDate:
    return ZhDate.from_datetime(datetime(d.year, d.month, d.day))


def lunar_md(d: date) -> tuple[int, int, bool]:
    """公历日期 → (农历月, 农历日, 是否闰月)。"""
    z = _solar_to_lunar(d)
    return int(z.lunar_month), int(z.lunar_day), bool(z.leap_month)


def is_birthday_today(
    birthday: date | None,
    calendar: str | None,
    *,
    today: date | None = None,
) -> bool:
    """是否处于生日权益日（按公历或农历月日匹配）。"""
    if birthday is None:
        return False
    day = today or date.today()
    cal = normalize_calendar(calendar)
    if cal == CAL_SOLAR:
        return birthday.month == day.month and birthday.day == day.day
    b_m, b_d, b_leap = lunar_md(birthday)
    t_m, t_d, t_leap = lunar_md(day)
    return b_m == t_m and b_d == t_d and b_leap == t_leap


def calendar_label(calendar: str | None) -> str:
    return "农历" if normalize_calendar(calendar) == CAL_LUNAR else "公历"


def convert_birthday_calendar(
    birthday: date | None,
    from_calendar: str | None,
    to_calendar: str | None,
) -> date | None:
    """历法切换时按「数字月日」重解释并换算存储用的公历日。

    - 公历→农历：把原公历的月/日当作农历月日，换算为对应公历日
    - 农历→公历：取出当前存储日的农历月日，写成同数字的公历日（非法日则钳到当月末日）
    """
    if birthday is None:
        return None
    src = normalize_calendar(from_calendar)
    dst = normalize_calendar(to_calendar)
    if src == dst:
        return birthday
    year = int(birthday.year)
    if src == CAL_SOLAR and dst == CAL_LUNAR:
        month = max(1, min(12, int(birthday.month)))
        day = max(1, min(30, int(birthday.day)))
        # 从大到小试日，避免农历无此日
        for d in range(day, 0, -1):
            try:
                z = ZhDate(year, month, d, leap_month=False)
                return z.to_datetime().date()
            except Exception:
                continue
        return birthday
    # LUNAR → SOLAR：农历月日 → 同数字公历
    lm, ld, _leap = lunar_md(birthday)
    month = max(1, min(12, int(lm)))
    day = max(1, min(31, int(ld)))
    while day >= 1:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, 1)


def format_birthday_display(birthday: date | None, calendar: str | None) -> str | None:
    """后台/小程序展示文案，如「公历 1990-05-01」或「农历 四月初七（对应公历 1990-05-01）」。"""
    if birthday is None:
        return None
    cal = normalize_calendar(calendar)
    if cal == CAL_SOLAR:
        return f"公历 {birthday.isoformat()}"
    z = _solar_to_lunar(birthday)
    # chinese() 形如「一九九〇年四月初七 庚午年 (马年)」；展示取月日段
    full = str(z.chinese() or "")
    md = full
    if "年" in full:
        md = full.split("年", 1)[1].split()[0]
    leap = "闰" if bool(z.leap_month) else ""
    if leap and not md.startswith("闰"):
        md = f"{leap}{md}"
    return f"农历 {md}（对应公历 {birthday.isoformat()}）"


MODE_OFF = "off"
MODE_POINTS = "points"
MODE_COUPON = "coupon"
MODE_BOTH = "points_and_coupon"
VALID_MODES = frozenset({MODE_OFF, MODE_POINTS, MODE_COUPON, MODE_BOTH})


def normalize_benefit_mode(raw: str | None) -> str:
    v = (raw or MODE_POINTS).strip().lower()
    return v if v in VALID_MODES else MODE_POINTS


def benefit_summary(
    *,
    mode: str,
    multiplier: int,
    coupon_name: str | None = None,
    calendar: str | None = None,
    is_today: bool = False,
) -> dict[str, Any]:
    """统一权益说明，供详情/配置下发。"""
    m = normalize_benefit_mode(mode)
    mult = max(1, int(multiplier or 1))
    cal = normalize_calendar(calendar) if calendar is not None else None
    coupon = (coupon_name or "").strip()
    parts: list[str] = []
    if m in (MODE_POINTS, MODE_BOTH) and mult > 1:
        parts.append(
            f"消费积分 ×{mult}（相对实付 1 元 = 1 分）"
        )
    if m in (MODE_COUPON, MODE_BOTH) and coupon:
        parts.append(f"赠券「{coupon}」")
    day_hint = calendar_label(cal) if cal else "公历或农历"
    if m == MODE_OFF or not parts:
        text = "当前未开启生日优惠"
        enabled = False
    else:
        text = f"生日当天（{day_hint}）" + "；".join(parts)
        enabled = True
    return {
        "enabled": enabled,
        "mode": m,
        "points_multiplier": mult,
        "coupon_name": coupon or None,
        "text": text,
        "is_birthday_today": bool(is_today),
        "how_to_edit": "门店 → 门店信息 → 业务参数 → 会员与优惠 →「生日优惠」相关项",
    }

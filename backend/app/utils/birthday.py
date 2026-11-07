"""生日日历（公历/农历）与权益日判定。

「今日」一律按北京时间（Asia/Shanghai）日历日判定。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from lunar_python import Lunar, Solar

CAL_SOLAR = "SOLAR"
CAL_LUNAR = "LUNAR"
VALID_CALENDARS = frozenset({CAL_SOLAR, CAL_LUNAR})
_BEIJING = ZoneInfo("Asia/Shanghai")


def normalize_calendar(raw: str | None) -> str:
    v = (raw or CAL_SOLAR).strip().upper()
    return v if v in VALID_CALENDARS else CAL_SOLAR


def today_beijing() -> date:
    """业务「今日」：北京时间日历日。"""
    return datetime.now(_BEIJING).date()


def _solar_to_lunar(d: date) -> Lunar:
    return Solar.fromYmd(d.year, d.month, d.day).getLunar()


def lunar_md(d: date) -> tuple[int, int, bool]:
    """公历日期 → (农历月, 农历日, 是否闰月)。"""
    z = _solar_to_lunar(d)
    month = int(z.getMonth())
    return abs(month), int(z.getDay()), month < 0


def solar_to_lunar_ymd(d: date) -> tuple[int, int, int, bool]:
    """公历日期 → (农历年, 农历月, 农历日, 是否闰月)。"""
    z = _solar_to_lunar(d)
    month = int(z.getMonth())
    return int(z.getYear()), abs(month), int(z.getDay()), month < 0


def lunar_ymd_to_solar(
    year: int,
    month: int,
    day: int,
    *,
    leap: bool = False,
) -> date:
    """农历年月日 → 对应公历日（落库锚点）。

    小程序/后台在历法=农历时，日期控件选中的数字就是农历月日，须经此函数再写入
    ``customers.birthday``（库内始终存公历）。非法农历日抛 ValueError。
    """
    try:
        lunar_month = -abs(int(month)) if leap else abs(int(month))
        z = Lunar.fromYmd(int(year), lunar_month, int(day))
        dt = z.getSolar()
    except Exception as exc:  # lunar-python 对越界农历日期抛异常
        raise ValueError(f"invalid lunar date {year}-{month}-{day}") from exc
    return date(int(dt.getYear()), int(dt.getMonth()), int(dt.getDay()))


def resolve_birthday_storage(ymd: date, calendar: str | None) -> date:
    """按历法解释用户选定的年月日，得到落库公历锚点。

    - SOLAR：数字即公历，原样返回
    - LUNAR：数字即农历年月日（非闰月），换算为公历后再存
    """
    cal = normalize_calendar(calendar)
    if cal == CAL_SOLAR:
        return ymd
    return lunar_ymd_to_solar(ymd.year, ymd.month, ymd.day, leap=False)


def resolve_birthday_input(raw: str, calendar: str | None) -> date:
    """解析字符串生日并按历法落库。"""
    return resolve_birthday_storage(parse_birthday_input(raw), calendar)


def birthday_for_picker(birthday: date | None, calendar: str | None) -> str | None:
    """给日期控件的 value：公历用 ISO；农历回填农历年-月-日数字。"""
    if birthday is None:
        return None
    cal = normalize_calendar(calendar)
    if cal == CAL_SOLAR:
        return birthday.isoformat()
    y, m, d, _leap = solar_to_lunar_ymd(birthday)
    return f"{y:04d}-{m:02d}-{d:02d}"


def is_birthday_today(
    birthday: date | None,
    calendar: str | None,
    *,
    today: date | None = None,
) -> bool:
    """是否处于生日权益日（按公历或农历月日匹配；今日=北京时间）。"""
    if birthday is None:
        return False
    day = today or today_beijing()
    cal = normalize_calendar(calendar)
    if cal == CAL_SOLAR:
        return birthday.month == day.month and birthday.day == day.day
    b_m, b_d, b_leap = lunar_md(birthday)
    t_m, t_d, t_leap = lunar_md(day)
    return b_m == t_m and b_d == t_d and b_leap == t_leap


def _as_beijing_date(value: date | datetime | None) -> date | None:
    """入会锚点统一成北京时间日历日。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(_BEIJING).date()
        return value.date()
    return value


def anniversary_md_matches(anchor: date, today: date) -> bool:
    """公历月日是否与今日同庆（2/29 非闰年落到 2/28）。"""
    if anchor.month == 2 and anchor.day == 29:
        if today.month == 2 and today.day == 29:
            return True
        # 非闰年：2/28 视为周年日
        try:
            date(today.year, 2, 29)
            return False
        except ValueError:
            return today.month == 2 and today.day == 28
    return anchor.month == today.month and anchor.day == today.day


def is_member_anniversary_today(
    birthday: date | None,
    member_activated_at: date | datetime | None,
    *,
    created_at: date | datetime | None = None,
    today: date | None = None,
    min_years: int = 1,
) -> bool:
    """无真实生日时，是否处于入会周年日。

    - 已填 ``birthday``：不算入会周年（应走生日权益）
    - 锚点优先 ``member_activated_at``，否则 ``created_at``
    - 默认须满 ``min_years`` 年（排除入会当天）
    - 今日按北京时间公历日
    """
    if birthday is not None:
        return False
    anchor = _as_beijing_date(member_activated_at) or _as_beijing_date(created_at)
    if anchor is None:
        return False
    day = today or today_beijing()
    years = day.year - anchor.year
    if years < max(0, int(min_years)):
        return False
    return anniversary_md_matches(anchor, day)


def calendar_label(calendar: str | None) -> str:
    return "农历" if normalize_calendar(calendar) == CAL_LUNAR else "公历"


def convert_birthday_calendar(
    birthday: date | None,
    from_calendar: str | None,
    to_calendar: str | None,
) -> date | None:
    """历法切换时的存储日换算（同日互转）。

    ``birthday`` 始终按公历落库；``birthday_calendar`` 只决定每年匹配规则与展示文案。
    公历↔农历切换表示「同一天」改用另一套历法解释，例如公历 1990-05-01
    ↔ 农历四月初七，存储的公历日不变；展示由 ``format_birthday_display`` 换算。

    历史上曾错误地按「数字月日重解释」（5/1 公历切农历变成五月初一），会导致
    与匹配/展示语义不一致，故此处恒等返回。
    """
    if birthday is None:
        return None
    # 保留 from/to 形参供 API 调用方传入；同日互转不改锚点公历日
    normalize_calendar(from_calendar)
    normalize_calendar(to_calendar)
    return birthday


def parse_birthday_input(raw: str) -> date:
    """解析 YYYY-MM-DD / YYYY-M-D / 斜杠分隔；非法则 ValueError。"""
    s = (raw or "").strip()
    if not s:
        raise ValueError("empty birthday")
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return date.fromisoformat(s)


def format_birthday_display(birthday: date | None, calendar: str | None) -> str | None:
    """后台/小程序展示文案，如「公历 1990-05-01」或「农历 四月初七（对应公历 1990-05-01）」。"""
    if birthday is None:
        return None
    cal = normalize_calendar(calendar)
    if cal == CAL_SOLAR:
        return f"公历 {birthday.isoformat()}"
    z = _solar_to_lunar(birthday)
    md = f"{z.getMonthInChinese()}月{z.getDayInChinese()}"
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

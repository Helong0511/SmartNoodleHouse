"""营销活动排期：绝对时间窗 / 每年月日（公历|农历）。

「今日」一律按北京时间（Asia/Shanghai）。
年度活动窗口外自动暂停、窗口内自动开启，无需逐年录入起止日期。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.utils.birthday import (
    CAL_LUNAR,
    CAL_SOLAR,
    lunar_md,
    normalize_calendar,
    solar_to_lunar_ymd,
    today_beijing,
)
from app.utils.product_visibility import _md_in_range

SCHEDULE_ABSOLUTE = "ABSOLUTE"
SCHEDULE_ANNUAL_MD = "ANNUAL_MD"
SCHEDULE_ALWAYS = "ALWAYS"
VALID_SCHEDULE_MODES = frozenset({SCHEDULE_ABSOLUTE, SCHEDULE_ANNUAL_MD})
VALID_TEMPLATE_SCHEDULE_MODES = frozenset({SCHEDULE_ALWAYS, SCHEDULE_ANNUAL_MD})


def normalize_schedule_mode(raw: str | None) -> str:
    v = str(raw or SCHEDULE_ABSOLUTE).strip().upper()
    return v if v in VALID_SCHEDULE_MODES else SCHEDULE_ABSOLUTE


def normalize_template_schedule_mode(raw: str | None) -> str:
    v = str(raw or SCHEDULE_ALWAYS).strip().upper()
    return v if v in VALID_TEMPLATE_SCHEDULE_MODES else SCHEDULE_ALWAYS


def normalize_schedule_calendar(raw: str | None) -> str:
    return normalize_calendar(raw)


def _clamp_md(month: int | None, day: int | None) -> tuple[int, int] | None:
    if month is None or day is None:
        return None
    try:
        m, d = int(month), int(day)
    except (TypeError, ValueError):
        return None
    if m < 1 or m > 12 or d < 1 or d > 31:
        return None
    return (m, d)


def today_md(today: date | None = None, *, calendar: str | None = None) -> tuple[int, int]:
    day = today or today_beijing()
    cal = normalize_schedule_calendar(calendar)
    if cal == CAL_LUNAR:
        m, d, _leap = lunar_md(day)
        return (m, d)
    return (day.month, day.day)


def is_in_annual_window(
    *,
    calendar: str | None,
    start_month: int | None,
    start_day: int | None,
    end_month: int | None = None,
    end_day: int | None = None,
    today: date | None = None,
) -> bool:
    """今日是否落在年度月日窗口内（含跨年/跨农历年底）。"""
    start = _clamp_md(start_month, start_day)
    if not start:
        return False
    end = _clamp_md(end_month, end_day) or start
    md = today_md(today, calendar=calendar)
    return _md_in_range(md, start, end)


def annual_season_token(
    *,
    calendar: str | None,
    start_month: int | None,
    start_day: int | None,
    end_month: int | None = None,
    end_day: int | None = None,
    today: date | None = None,
) -> str:
    """本季发券幂等后缀：公历 S{年} / 农历 L{农历年}。

    跨年窗口（如 12/21～01/05）在元旦后仍归属起始侧年份，避免一季拆两把键。
    """
    day = today or today_beijing()
    cal = normalize_schedule_calendar(calendar)
    start = _clamp_md(start_month, start_day) or (1, 1)
    end = _clamp_md(end_month, end_day) or start
    if cal == CAL_LUNAR:
        y, _m, _d, _leap = solar_to_lunar_ymd(day)
        # 跨农历年底：元旦后若仍在窗口前半（md<=end 且 start>end），农历年已进新年，
        # 用「窗口起始侧」的农历年：取 today 的农历年即可（腊月属上一年农历年）。
        return f"L{y}"
    md = (day.month, day.day)
    if start <= end:
        return f"S{day.year}"
    # 公历跨年：落在年末段用当年；落在年初段用去年
    if md >= start:
        return f"S{day.year}"
    return f"S{day.year - 1}"


def template_in_issue_window(tmpl: Any, *, today: date | None = None) -> bool:
    """券模板当前是否允许新发。ALWAYS=始终可发；ANNUAL_MD=仅月日窗内。"""
    mode = normalize_template_schedule_mode(getattr(tmpl, "schedule_mode", None))
    if mode != SCHEDULE_ANNUAL_MD:
        return True
    return is_in_annual_window(
        calendar=getattr(tmpl, "schedule_calendar", None),
        start_month=getattr(tmpl, "annual_start_month", None),
        start_day=getattr(tmpl, "annual_start_day", None),
        end_month=getattr(tmpl, "annual_end_month", None),
        end_day=getattr(tmpl, "annual_end_day", None),
        today=today,
    )


def campaign_in_schedule_window(camp: Any, *, today: date | None = None, now=None) -> bool:
    """活动当前是否应处于「进行中」窗口。

    - ANNUAL_MD：按公历/农历月日
    - ABSOLUTE：按 start_at/end_at（缺省起=已开始，缺省止=未结束）
    """
    mode = normalize_schedule_mode(getattr(camp, "schedule_mode", None))
    if mode == SCHEDULE_ANNUAL_MD:
        return is_in_annual_window(
            calendar=getattr(camp, "schedule_calendar", None),
            start_month=getattr(camp, "annual_start_month", None),
            start_day=getattr(camp, "annual_start_day", None),
            end_month=getattr(camp, "annual_end_month", None),
            end_day=getattr(camp, "annual_end_day", None),
            today=today,
        )
    # ABSOLUTE
    from app.core.response import naive_local_now

    ts = now if now is not None else naive_local_now()
    start_at = getattr(camp, "start_at", None)
    end_at = getattr(camp, "end_at", None)
    if start_at and start_at > ts:
        return False
    if end_at and end_at <= ts:
        return False
    return True


def format_annual_label(
    *,
    calendar: str | None,
    start_month: int | None,
    start_day: int | None,
    end_month: int | None = None,
    end_day: int | None = None,
) -> str:
    start = _clamp_md(start_month, start_day)
    if not start:
        return ""
    end = _clamp_md(end_month, end_day) or start
    prefix = "农历" if normalize_schedule_calendar(calendar) == CAL_LUNAR else "公历"
    if start == end:
        return f"{prefix}{start[0]}月{start[1]}日"
    return f"{prefix}{start[0]}月{start[1]}日～{end[0]}月{end[1]}日"

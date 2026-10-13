"""商品展示规则：常显 / 日期区间 / 每年月日（公历|农历）/ 顾客今日生日。

「今日」一律按北京时间（Asia/Shanghai）日历日判定，不跟服务器/手机本地时区。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.utils.birthday import CAL_LUNAR, lunar_md, normalize_calendar, today_beijing

VIS_ALWAYS = "ALWAYS"
VIS_DATE_RANGE = "DATE_RANGE"
VIS_ANNUAL_MD = "ANNUAL_MD"
VIS_BIRTHDAY = "BIRTHDAY_TODAY"
VALID_VISIBILITY_RULES = frozenset(
    {VIS_ALWAYS, VIS_DATE_RANGE, VIS_ANNUAL_MD, VIS_BIRTHDAY}
)


def normalize_visibility_rule(raw: str | None) -> str:
    v = str(raw or VIS_ALWAYS).strip().upper()
    return v if v in VALID_VISIBILITY_RULES else VIS_ALWAYS


def normalize_visibility_calendar(raw: str | None) -> str:
    """展示历法：SOLAR=公历 / LUNAR=农历（主要用于每年月日规则）。"""
    return normalize_calendar(raw)


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if type(value) is date:
        return value
    if hasattr(value, "date") and callable(value.date):
        try:
            return value.date()
        except Exception:
            pass
    text = str(value).strip()[:10]
    if not text:
        return None
    return date.fromisoformat(text)


def _md_tuple(d: date, *, calendar: str) -> tuple[int, int]:
    """取月日元组；农历忽略闰月标记（与区间比较一致，精确生日仍走 is_birthday_today）。"""
    cal = normalize_visibility_calendar(calendar)
    if cal == CAL_LUNAR:
        m, day, _leap = lunar_md(d)
        return (m, day)
    return (d.month, d.day)


def _md_in_range(md: tuple[int, int], start: tuple[int, int], end: tuple[int, int]) -> bool:
    if start <= end:
        return start <= md <= end
    # 跨年/跨农历年底，如 12-20～01-05 或 腊月二十～正月初五
    return md >= start or md <= end


def is_product_visible(
    product: Any,
    *,
    today: date | None = None,
    is_birthday_today: bool = False,
) -> bool:
    """判断商品今日是否应对顾客/收银可见（今日=北京时间）。"""
    day = today or today_beijing()
    rule = normalize_visibility_rule(getattr(product, "visibility_rule", None))
    if rule == VIS_ALWAYS:
        return True
    if rule == VIS_BIRTHDAY:
        return bool(is_birthday_today)
    vf = _as_date(getattr(product, "visible_from", None))
    vt = _as_date(getattr(product, "visible_to", None))
    cal = normalize_visibility_calendar(getattr(product, "visibility_calendar", None))
    if rule == VIS_DATE_RANGE:
        # 日期区间始终按「落库的公历绝对日期」比较（具体活动周）
        if vf and day < vf:
            return False
        if vt and day > vt:
            return False
        # 未填起止则视为不可见，避免误配成「永远可见」
        return vf is not None or vt is not None
    if rule == VIS_ANNUAL_MD:
        if not vf:
            return False
        md = _md_tuple(day, calendar=cal)
        start = _md_tuple(vf, calendar=cal)
        end = _md_tuple(vt, calendar=cal) if vt else start
        return _md_in_range(md, start, end)
    return True


def visibility_payload(product: Any) -> dict[str, Any]:
    rule = normalize_visibility_rule(getattr(product, "visibility_rule", None))
    cal = normalize_visibility_calendar(getattr(product, "visibility_calendar", None))
    vf = _as_date(getattr(product, "visible_from", None))
    vt = _as_date(getattr(product, "visible_to", None))
    return {
        "visibility_rule": rule,
        "visibility_calendar": cal,
        "visible_from": vf.isoformat() if vf else None,
        "visible_to": vt.isoformat() if vt else None,
    }


def filter_visible_products(
    products: list[Any],
    *,
    today: date | None = None,
    is_birthday_today: bool = False,
) -> list[Any]:
    day = today or today_beijing()
    return [
        p
        for p in products
        if is_product_visible(p, today=day, is_birthday_today=is_birthday_today)
    ]


def filter_visible_product_dicts(
    items: list[dict[str, Any]],
    *,
    today: date | None = None,
    is_birthday_today: bool = False,
) -> list[dict[str, Any]]:
    """菜单 bundle 等已序列化条目按规则过滤。"""
    day = today or today_beijing()

    class _Proxy:
        __slots__ = (
            "visibility_rule",
            "visibility_calendar",
            "visible_from",
            "visible_to",
        )

        def __init__(self, d: dict[str, Any]):
            self.visibility_rule = d.get("visibility_rule")
            self.visibility_calendar = d.get("visibility_calendar")
            self.visible_from = d.get("visible_from")
            self.visible_to = d.get("visible_to")

    out: list[dict[str, Any]] = []
    for it in items:
        if is_product_visible(
            _Proxy(it), today=day, is_birthday_today=is_birthday_today
        ):
            out.append(it)
    return out

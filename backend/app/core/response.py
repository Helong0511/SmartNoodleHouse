from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import logging
from typing import Any, Generic, Optional, TypeVar
from zoneinfo import ZoneInfo

from fastapi import Request
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)
T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    code: int = 0
    message: str = "success"
    data: Optional[T] = None
    request_id: Optional[str] = None


class PaginatedData(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


def success(data: Any = None, message: str = "success", request: Request | None = None) -> dict:
    rid = getattr(request.state, "request_id", None) if request else None
    return {"code": 0, "message": message, "data": data, "request_id": rid}


def error(code: int, message: str, request: Request | None = None) -> dict:
    rid = getattr(request.state, "request_id", None) if request else None
    return {"code": code, "message": message, "data": None, "request_id": rid}


def _biz_timezone() -> ZoneInfo:
    settings = get_settings()
    from app.services.system_settings_service import SystemSettingsService

    name = SystemSettingsService.resolved_str_sync("app_timezone", settings.app_timezone or "Asia/Shanghai")
    try:
        return ZoneInfo(name.strip() or "Asia/Shanghai")
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def now_local() -> datetime:
    return datetime.now(_biz_timezone())


def ensure_local_aware(dt: datetime | None) -> datetime | None:
    """MySQL 读出的本地时间常为 naive，与 now_local() 比较前补齐时区。"""
    if dt is None:
        return None
    tz = _biz_timezone()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def naive_local_now() -> datetime:
    """供 ORM/SQL 绑定的本地 naive 时间（与 MySQL DATETIME 一致）。"""
    return now_local().replace(tzinfo=None)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def money(value: Decimal | str | int | float | None) -> Decimal:
    """金额统一两位小数，四舍五入（ROUND_HALF_UP）。

    None 或非法值兜底为 Decimal("0.00")，避免回调金额缺失时崩溃。
    调用方应在拿到结果后自行判断是否为 0 并决定是否拒绝。
    支付入账等关键路径请改用 require_money()，禁止把缺失金额当 0 入账。
    """
    from decimal import ROUND_HALF_UP

    if value is None:
        return Decimal("0.00")
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        logger.warning("money() 收到非法金额值: %r，兜底为 0.00", value)
        return Decimal("0.00")


def require_money(value: Decimal | str | int | float | None, *, field: str = "amount") -> Decimal:
    """支付/入账专用：None 或非法金额直接拒绝，绝不兜底为 0。"""
    from decimal import ROUND_HALF_UP

    from app.core.exceptions import AppException

    if value is None:
        raise AppException(40001, f"{field} 不能为空")
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise AppException(40001, f"{field} 格式非法")


def apply_date_range(q, col, date_from: str | None, date_to: str | None):
    """列表日期过滤：日期-only（len<=10）的 date_to 含当天整天（开区间 < end+1day）。

    非法日期抛 AppException(40001)，禁止静默忽略导致「全量返回」。
    """
    from app.core.exceptions import AppException

    if date_from:
        raw = str(date_from).strip()
        try:
            q = q.where(col >= datetime.fromisoformat(raw))
        except ValueError:
            raise AppException(40001, f"date_from 格式非法: {date_from}")
    if date_to:
        raw = str(date_to).strip()
        try:
            end = datetime.fromisoformat(raw)
            if len(raw) <= 10:
                end = end + timedelta(days=1)
            q = q.where(col < end)
        except ValueError:
            raise AppException(40001, f"date_to 格式非法: {date_to}")
    return q


def apply_datetime_range(q, col, date_from: datetime | None, date_to: datetime | None):
    """FastAPI 已解析为 datetime 的查询参数：仅传日期时多为当天 00:00:00，需把 date_to 扩到次日。"""
    if date_from is not None:
        q = q.where(col >= date_from)
    if date_to is not None:
        end = date_to
        # 无时分秒（或全 0）视为自然日截止，扩到次日开区间
        if (
            end.hour == 0
            and end.minute == 0
            and end.second == 0
            and end.microsecond == 0
        ):
            end = end + timedelta(days=1)
            q = q.where(col < end)
        else:
            q = q.where(col <= date_to)
    return q


def calc_pages(total: int, page_size: int) -> int:
    if page_size <= 0:
        return 0
    return (total + page_size - 1) // page_size

from datetime import datetime, timezone
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
    """
    from decimal import ROUND_HALF_UP

    if value is None:
        return Decimal("0.00")
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        logger.warning("money() 收到非法金额值: %r，兜底为 0.00", value)
        return Decimal("0.00")


def calc_pages(total: int, page_size: int) -> int:
    if page_size <= 0:
        return 0
    return (total + page_size - 1) // page_size

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Generic, Optional, TypeVar
from zoneinfo import ZoneInfo

from fastapi import Request
from pydantic import BaseModel

from app.core.config import get_settings

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


def now_local() -> datetime:
    return datetime.now(ZoneInfo(get_settings().app_timezone))


def ensure_local_aware(dt: datetime | None) -> datetime | None:
    """MySQL 读出的本地时间常为 naive，与 now_local() 比较前补齐时区。"""
    if dt is None:
        return None
    tz = ZoneInfo(get_settings().app_timezone)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def naive_local_now() -> datetime:
    """供 ORM/SQL 绑定的本地 naive 时间（与 MySQL DATETIME 一致）。"""
    return now_local().replace(tzinfo=None)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def money(value: Decimal | str | int | float) -> Decimal:
    """金额统一两位小数，四舍五入（ROUND_HALF_UP）。"""
    from decimal import ROUND_HALF_UP

    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calc_pages(total: int, page_size: int) -> int:
    if page_size <= 0:
        return 0
    return (total + page_size - 1) // page_size

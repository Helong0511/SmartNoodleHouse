from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
from typing import Any, Generic, Optional, TypeVar
from uuid import UUID, uuid4
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


def bind_request_id(request: Request) -> str:
    """中间件短路返回时 RequestID 尚未执行，这里补上同一枚举。"""
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str) and existing:
        return existing
    raw = (request.headers.get("X-Request-ID") or "").strip()
    rid = raw[:64] if raw else uuid4().hex
    request.state.request_id = rid
    return rid


def json_error_response(request: Request, *, status_code: int, code: int, message: str):
    """限流 / 白名单等未进入路由的拒绝：信封含 data 与 request_id，并回写响应头。"""
    from fastapi.responses import JSONResponse

    rid = bind_request_id(request)
    return JSONResponse(
        status_code=status_code,
        content=error(code, message, request),
        headers={"X-Request-ID": rid},
    )


def success_no_store(
    data: Any = None,
    message: str = "success",
    request: Request | None = None,
):
    """金额/状态类 GET：信封成功 + Cache-Control: no-store，防中间缓存陈旧数据。"""
    from fastapi.responses import JSONResponse

    return JSONResponse(
        content=success(data, message=message, request=request),
        headers={"Cache-Control": "no-store"},
    )


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


def _to_naive_china(dt: datetime) -> datetime:
    """查询边界统一为中国本地 naive，与 MySQL DATETIME 一致。"""
    if dt.tzinfo is not None:
        return dt.astimezone(_biz_timezone()).replace(tzinfo=None)
    return dt


def apply_date_range(q, col, date_from: str | None, date_to: str | None):
    """列表日期过滤（中国时区自然日）：date_to 为日期-only 时含当天整天（开区间 < end+1day）。

    非法日期抛 AppException(40001)，禁止静默忽略导致「全量返回」。
    带偏移的 ISO 串会先换算到 Asia/Shanghai 再剥时区，避免与库内 naive 错位。
    """
    from app.core.exceptions import AppException

    if date_from:
        raw = str(date_from).strip()
        try:
            start = _to_naive_china(datetime.fromisoformat(raw))
            q = q.where(col >= start)
        except ValueError:
            raise AppException(40001, f"date_from 格式非法: {date_from}")
    if date_to:
        raw = str(date_to).strip()
        try:
            end = _to_naive_china(datetime.fromisoformat(raw))
            if len(raw) <= 10:
                end = end + timedelta(days=1)
            q = q.where(col < end)
        except ValueError:
            raise AppException(40001, f"date_to 格式非法: {date_to}")
    return q


def apply_datetime_range(q, col, date_from: datetime | None, date_to: datetime | None):
    """FastAPI 已解析为 datetime 的查询参数：按中国本地 naive 比较；仅传日期时扩到次日。"""
    if date_from is not None:
        q = q.where(col >= _to_naive_china(date_from))
    if date_to is not None:
        end = _to_naive_china(date_to)
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
            q = q.where(col <= end)
    return q


def calc_pages(total: int, page_size: int) -> int:
    if page_size <= 0:
        return 0
    return (total + page_size - 1) // page_size


def serialize_dt(dt: datetime | date | None) -> str | None:
    """API 出参时间：中国时区 ISO 8601（默认 Asia/Shanghai，如 +08:00）。

    date → YYYY-MM-DD；datetime naive 按业务时区（APP_TIMEZONE）补齐后输出带偏移。
    MySQL DATETIME 存中国本地 naive，出参一律带显式偏移，禁止裸时间戳 / 无时区字符串。
    """
    if dt is None:
        return None
    if type(dt) is date and not isinstance(dt, datetime):
        return dt.isoformat()
    aware = ensure_local_aware(dt)  # type: ignore[arg-type]
    return aware.isoformat() if aware is not None else None


def json_default(obj: Any) -> Any:
    """统一 JSON 序列化钩子：Decimal/datetime/UUID/set/bytes。"""
    if isinstance(obj, Decimal):
        return str(money(obj))
    if isinstance(obj, datetime):
        return serialize_dt(obj)
    if type(obj) is date:
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def dumps_json(obj: Any, *, ensure_ascii: bool = False, compact: bool = False) -> str:
    """安全 JSON：allow_nan=False，禁止 NaN/Infinity 污染前端 JSON.parse。"""
    separators = (",", ":") if compact else None
    return json.dumps(
        obj,
        ensure_ascii=ensure_ascii,
        allow_nan=False,
        default=json_default,
        separators=separators,
    )

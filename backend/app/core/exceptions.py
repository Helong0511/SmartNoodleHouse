import logging

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError

logger = logging.getLogger(__name__)


def _http_status_from_biz_code(code: int) -> int:
    """由业务码推导 HTTP 状态：401xx→401、403xx→403、404xx→404、429xx→429、503xx→503、5xxxx→500，其余 400。"""
    if 40100 <= code < 40200:
        return 401
    if 40300 <= code < 40400:
        return 403
    if 40400 <= code < 40500:
        return 404
    if 41000 <= code < 41100:
        return 410
    if 42900 <= code < 43000:
        return 429
    if 50300 <= code < 50400:
        return 503
    if code >= 50000:
        return 500
    return 400


class AppException(Exception):
    def __init__(self, code: int, message: str, status_code: int | None = None):
        self.code = code
        self.message = message
        self.status_code = int(status_code) if status_code is not None else _http_status_from_biz_code(int(code))
        super().__init__(message)


def _format_validation_errors(errors: list) -> str:
    """把 Pydantic/FastAPI 校验错误压成一条中文摘要，避免前端只看到笼统「参数有误」。"""
    parts: list[str] = []
    for err in errors[:8]:
        loc = err.get("loc") or ()
        # 跳过 body/query/path 前缀
        path = ".".join(str(x) for x in loc if x not in ("body", "query", "path", "header"))
        msg = str(err.get("msg") or "校验失败")
        if path:
            parts.append(f"{path}: {msg}")
        else:
            parts.append(msg)
    if not parts:
        return "请求参数有误"
    summary = "；".join(parts)
    if len(errors) > 8:
        summary = f"{summary}（另有 {len(errors) - 8} 项）"
    return summary


async def app_exception_handler(request: Request, exc: AppException):
    from fastapi.responses import JSONResponse

    from app.core.response import error

    return JSONResponse(status_code=exc.status_code, content=error(exc.code, exc.message, request))


async def http_exception_handler(request: Request, exc: HTTPException):
    from fastapi.responses import JSONResponse

    from app.core.response import error

    return JSONResponse(status_code=exc.status_code, content=error(exc.status_code, str(exc.detail), request))


async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    """校验失败统一为业务信封，避免 FastAPI 默认 {detail:[...]} 与小程序/前端解析脱节。"""
    from fastapi.responses import JSONResponse

    from app.core.response import error

    return JSONResponse(
        status_code=422,
        content=error(40001, _format_validation_errors(list(exc.errors())), request),
    )


def _integrity_user_message(exc: Exception) -> str:
    import re

    from sqlalchemy.exc import IntegrityError

    if not isinstance(exc, IntegrityError):
        return "数据冲突，请检查是否重复"
    orig = str(getattr(exc, "orig", exc))
    m = re.search(r"Duplicate entry '([^']+)' for key '([^']+)'", orig)
    if not m:
        return "数据冲突，请检查是否重复"
    value, key = m.group(1), m.group(2)
    if "product_no" in key:
        return f"商品编号「{value}」已存在，请换一个"
    if "openid" in key or "alipay_user_id" in key:
        return "该账号已登录过，请重试（若反复出现请清缓存后重新编译）"
    if "order_no" in key or "idempotency" in key:
        return "订单或请求重复，请勿重复提交"
    if "code" in key:
        return f"编码「{value}」已存在，请换一个"
    return f"「{value}」已存在，请换一个"


async def sqlalchemy_integrity_handler(request: Request, exc: Exception):
    from fastapi.responses import JSONResponse
    from sqlalchemy.exc import IntegrityError

    from app.core.response import error

    if not isinstance(exc, IntegrityError):
        raise exc
    return JSONResponse(status_code=400, content=error(40002, _integrity_user_message(exc), request))


async def sqlalchemy_operational_handler(request: Request, exc: Exception):
    """数据库连不上时返回 JSON，避免小程序只看到 Internal Server Error / request:ok。"""
    from fastapi.responses import JSONResponse
    from sqlalchemy.exc import OperationalError

    from app.core.config import get_settings
    from app.core.response import error

    if not isinstance(exc, OperationalError):
        raise exc
    settings = get_settings()
    detail = str(getattr(exc, "orig", exc))
    msg = "数据库连接失败，请检查 .env 的 DATABASE_URL（MySQL 账号、密码、库 smart_noodle_house）"
    if settings.is_development and detail:
        msg = f"{msg}：{detail}"
    return JSONResponse(status_code=503, content=error(50000, msg, request))


async def generic_exception_handler(request: Request, exc: Exception):
    """P0-5：兜底异常处理器，返回 JSON 而非 HTML 500，避免小程序只看到 request:ok 但解析失败。"""
    from fastapi.responses import JSONResponse

    from app.core.config import get_settings
    from app.core.response import error

    logger.exception("Unhandled exception: %s %s", request.method, request.url.path)
    settings = get_settings()
    msg = "服务器内部错误，请稍后重试"
    if settings.is_development:
        msg = f"{msg}：{type(exc).__name__}: {exc}"
    return JSONResponse(status_code=500, content=error(50000, msg, request))

from fastapi import HTTPException, Request


class AppException(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def app_exception_handler(request: Request, exc: AppException):
    from fastapi.responses import JSONResponse

    from app.core.response import error

    return JSONResponse(status_code=exc.status_code, content=error(exc.code, exc.message, request))


async def http_exception_handler(request: Request, exc: HTTPException):
    from fastapi.responses import JSONResponse

    from app.core.response import error

    return JSONResponse(status_code=exc.status_code, content=error(exc.status_code, str(exc.detail), request))


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

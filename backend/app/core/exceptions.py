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

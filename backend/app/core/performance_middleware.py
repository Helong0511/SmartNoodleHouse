"""性能中间件（Task 12）：

- PerformanceMetricsMiddleware：按路由模板记录耗时（Redis 滚动窗口），>800ms 结构化慢日志；
- JsonGZipMiddleware：仅对 >1KB 的 application/json 响应且客户端接受 gzip 时压缩。
"""
from __future__ import annotations

import gzip
import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.perf_metrics import SLOW_REQUEST_MS, normalize_route, record_request_timing

logger = logging.getLogger(__name__)

_SKIP_PREFIXES = ("/health", "/ready", "/docs", "/redoc", "/openapi.json", "/uploads")
GZIP_MIN_BYTES = 1024


class PerformanceMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(_SKIP_PREFIXES):
            return await call_next(request)
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            route_obj = request.scope.get("route")
            template = getattr(route_obj, "path", None)
            route = normalize_route(path, template)
            if route != "unknown":
                await record_request_timing(route, duration_ms)
            if duration_ms > SLOW_REQUEST_MS:
                logger.warning(
                    "slow_request method=%s route=%s path=%s status=%s cost_ms=%.1f",
                    request.method,
                    route,
                    path,
                    status_code,
                    duration_ms,
                )


class JsonGZipMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        accept = (request.headers.get("accept-encoding") or "").lower()
        content_type = response.headers.get("content-type") or ""
        if "gzip" not in accept or "application/json" not in content_type.lower():
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        vary = response.headers.get("vary")
        vary_value = "Accept-Encoding" if not vary else f"{vary}, Accept-Encoding"
        if len(body) <= GZIP_MIN_BYTES:
            headers = dict(response.headers)
            headers["vary"] = vary_value
            return Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                media_type=content_type,
            )
        compressed = gzip.compress(body, compresslevel=6)
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers["content-encoding"] = "gzip"
        headers["content-length"] = str(len(compressed))
        headers["vary"] = vary_value
        return Response(
            content=compressed,
            status_code=response.status_code,
            headers=headers,
            media_type=content_type,
        )

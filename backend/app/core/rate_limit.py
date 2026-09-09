"""API 限流中间件（Redis 滑动窗口近似：固定窗口计数）"""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.ip_allowlist import client_ip
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

# path 前缀 → (limit, window_seconds)
SENSITIVE_LIMITS = {
    "/api/v1/auth/login": (20, 60),
    "/api/v1/payments/": (60, 60),
    "/api/v1/welcome/": (30, 60),
    "/api/v1/miniprogram/": (60, 60),
    "/api/v1/client-logs": (120, 60),
    "/api/v1/delivery/meituan/notify": (300, 60),
    "/api/v1/delivery/eleme/notify": (300, 60),
}
DEFAULT_LIMIT = (120, 60)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        if settings.is_development and not getattr(settings, "enable_rate_limit", True):
            return await call_next(request)

        path = request.url.path
        limit, window = DEFAULT_LIMIT
        for prefix, cfg in SENSITIVE_LIMITS.items():
            if path.startswith(prefix) or path == prefix.rstrip("/"):
                limit, window = cfg
                break

        key = f"snh:rl:{client_ip(request) or 'unknown'}:{path.split('?')[0]}"

        try:
            r = await get_redis()
            count = await r.incr(key)
            if count == 1:
                await r.expire(key, window)
            if count > limit:
                return JSONResponse(
                    status_code=429,
                    content={"code": 429, "message": "请求过于频繁，请稍后再试", "data": None},
                )
        except Exception as e:
            # 生产环境敏感接口：Redis 不可用时 fail-closed，禁止旁路限流
            is_sensitive = any(
                path.startswith(p.rstrip("/")) or path == p.rstrip("/") for p in SENSITIVE_LIMITS
            )
            if settings.is_production and is_sensitive:
                logger.error("限流 Redis 不可用，拒绝敏感请求: %s", e)
                return JSONResponse(
                    status_code=503,
                    content={"code": 503, "message": "服务繁忙，请稍后重试", "data": None},
                )
            logger.warning("限流 Redis 不可用，跳过: %s", e)

        return await call_next(request)

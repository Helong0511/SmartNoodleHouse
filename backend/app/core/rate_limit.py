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
    "/api/auth/login": (10, 60),
    "/api/auth/verify-password": (20, 60),
    "/api/payments/": (60, 60),
    "/api/welcome/": (30, 60),
    "/api/miniprogram/": (60, 60),
    "/api/client-logs": (120, 60),
    "/api/delivery/meituan/notify": (300, 60),
    "/api/delivery/eleme/notify": (300, 60),
}
DEFAULT_LIMIT = (120, 60)

# INCR+EXPIRE 原子化：避免两步间失败留下无 TTL 计数键导致该 IP+路径永久 429
_INCR_SLIDING = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
else
  local ttl = redis.call('TTL', KEYS[1])
  if ttl == -1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
  end
end
return c
"""


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            from app.services.system_settings_service import SystemSettingsService

            await SystemSettingsService.touch_cache()
        except Exception:
            pass

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
            count = int(await r.eval(_INCR_SLIDING, 1, key, window))
            if count > limit:
                return JSONResponse(
                    status_code=429,
                    content={"code": 429, "message": "请求过于频繁，请稍后再试", "data": None},
                )
        except Exception as e:
            # Redis 仅作限流加速；宕机时降级放行，避免全站 503（业务以 MySQL 为准）。
            # 业务侧仍受认证 / 入侵检测 / SQL 注入检测等多层防护兜底。
            logger.error("限流 Redis 不可用，降级放行: %s", e)

        return await call_next(request)

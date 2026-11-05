"""API 限流中间件（Redis 固定窗口计数；路径分桶防动态 URL key 爆炸）"""
from __future__ import annotations

import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.ip_allowlist import client_ip
from app.core.redis_client import get_redis
from app.core.response import json_error_response

logger = logging.getLogger(__name__)

# path 前缀 → (limit, window_seconds)
SENSITIVE_LIMITS = {
    "/api/auth/login": (10, 60),
    "/api/miniprogram/wechat/login": (10, 60),
    "/api/miniprogram/alipay/login": (10, 60),
    "/api/miniprogram/auth/refresh": (30, 60),
    "/api/auth/verify-password": (20, 60),
    "/api/payments/": (60, 60),
    "/api/welcome/": (30, 60),
    "/api/miniprogram/": (60, 60),
    "/api/client-logs": (120, 60),
    "/api/delivery/meituan/notify": (300, 60),
    "/api/delivery/eleme/notify": (300, 60),
}
DEFAULT_LIMIT = (120, 60)


def _fail_closed_prefixes(raw: str) -> list[str]:
    out: list[str] = []
    for part in (raw or "").split(","):
        p = part.strip()
        if p:
            out.append(p)
    return out


def path_requires_fail_closed(path: str, prefixes: list[str]) -> bool:
    for prefix in prefixes:
        if path.startswith(prefix) or path == prefix.rstrip("/"):
            return True
    return False


def _rate_limit_bucket(path: str) -> str:
    """将请求路径归一到限流桶，避免动态 ID/随机路径制造海量 Redis key。

    已配置敏感限流前缀按各自策略独立分桶；其他 /api 路径按一级资源分桶。
    """
    normalized = (path or "/").split("?", 1)[0]
    for prefix in SENSITIVE_LIMITS:
        # 通用小程序/外卖前缀只定义限额，不应把所有业务路由挤进同一个桶。
        if prefix in ("/api/miniprogram/", "/api/delivery/"):
            continue
        if normalized.startswith(prefix) or normalized == prefix.rstrip("/"):
            return prefix.rstrip("/")
    parts = [part for part in normalized.strip("/").split("/") if part]
    if len(parts) >= 3 and parts[0] == "api":
        return f"/api/{parts[1]}/{parts[2]}"
    if len(parts) >= 2 and parts[0] == "api":
        return f"/api/{parts[1]}"
    return f"/{parts[0]}" if parts else "/"

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

        from app.core.cache_keys import rate_limit_key

        key = rate_limit_key(client_ip(request) or "unknown", _rate_limit_bucket(path))

        try:
            r = await get_redis()
            count = int(await r.eval(_INCR_SLIDING, 1, key, window))
            if count > limit:
                return json_error_response(
                    request,
                    status_code=429,
                    code=429,
                    message="请求过于频繁，请稍后再试",
                )
        except Exception as e:
            # 默认 fail-open：Redis 仅加速层，避免全站 503。
            # 敏感前缀可按 RATE_LIMIT_FAIL_CLOSED_PATHS 单独 fail-closed（503）。
            raw_paths = getattr(settings, "rate_limit_fail_closed_paths", "")
            prefixes = _fail_closed_prefixes(raw_paths if isinstance(raw_paths, str) else "")
            if path_requires_fail_closed(path, prefixes):
                logger.error("限流 Redis 不可用，敏感路径 fail-closed: path=%s err=%s", path, e)
                return json_error_response(
                    request,
                    status_code=503,
                    code=50301,
                    message="限流服务暂不可用，请稍后重试",
                )
            logger.error("限流 Redis 不可用，降级放行: %s", e)

        return await call_next(request)

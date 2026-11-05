"""API 限流中间件（Redis Token Bucket；路径分桶防动态 URL key 爆炸）"""
from __future__ import annotations

import hashlib
import logging
import time

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

# Redis 故障时限流中间件可能在高 QPS 下逐请求触发异常。
# 日志必须限频，避免故障本身再引发磁盘/日志采集 IO 雪崩。
_REDIS_ERROR_LOG_INTERVAL_SEC = 30.0
_last_redis_error_log_at = 0.0
_suppressed_redis_error_logs = 0


def _log_redis_failure(path: str, error: Exception, *, fail_closed: bool) -> None:
    global _last_redis_error_log_at, _suppressed_redis_error_logs
    now = time.monotonic()
    if now - _last_redis_error_log_at < _REDIS_ERROR_LOG_INTERVAL_SEC:
        _suppressed_redis_error_logs += 1
        return
    suppressed = _suppressed_redis_error_logs
    _suppressed_redis_error_logs = 0
    _last_redis_error_log_at = now
    if fail_closed:
        logger.error(
            "限流 Redis 不可用，敏感路径 fail-closed: path=%s suppressed=%s err=%s",
            path, suppressed, error,
        )
    else:
        logger.warning(
            "限流 Redis 不可用，降级放行: path=%s suppressed=%s err=%s",
            path, suppressed, error,
        )


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
    """将请求路径归一到稳定的资源级限流桶，避免动态 URL 参数制造高基数 Redis key。

    敏感接口在 dispatch 中仍按 SENSITIVE_LIMITS 配置限额；这里仅决定键分组。
    普通 API 只使用 /api/{resource} 两段，绝不把订单 ID、用户 ID、随机 slug
    或任意后续路径段写入 Redis key。代价是同一资源下的普通接口共享限流额度，
    这是有意的容量保护；需要独立额度的固定路由应加入 SENSITIVE_LIMITS。
    """
    normalized = (path or "/").split("?", 1)[0]
    for prefix in SENSITIVE_LIMITS:
        # 通用小程序/外卖前缀只提供默认限额；具体资源继续走固定分片，
        # 以免整个渠道共用一个桶。具体登录/支付/回调路由保持独立桶。
        if prefix in ("/api/miniprogram/", "/api/delivery/"):
            continue
        if normalized.startswith(prefix) or normalized == prefix.rstrip("/"):
            return prefix.rstrip("/")
    parts = [part for part in normalized.strip("/").split("/") if part]
    if len(parts) >= 2 and parts[0].lower() == "api":
        resource = parts[1].lower()
        shard = int.from_bytes(
            hashlib.blake2s(resource.encode("utf-8", errors="replace"), digest_size=2).digest(),
            "big",
        ) % 64
        return f"/api/_resource_{shard:02d}"
    if parts:
        resource = parts[0].lower()
        shard = int.from_bytes(
            hashlib.blake2s(resource.encode("utf-8", errors="replace"), digest_size=2).digest(),
            "big",
        ) % 64
        return f"/_path_{shard:02d}"
    return "/"

# 原子 Token Bucket：避免固定窗口在边界处出现接近 2 倍突发。
# Redis TIME 作为权威时钟，Hash 仅保存 tokens/last_ms，空间恒定，不使用高基数 ZSET。
_TOKEN_BUCKET = """
local tm = redis.call('TIME')
local now_ms = tonumber(tm[1]) * 1000 + math.floor(tonumber(tm[2]) / 1000)
local capacity = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens'))
local last_ms = tonumber(redis.call('HGET', KEYS[1], 'last_ms'))
if tokens == nil or last_ms == nil then
  tokens = capacity
  last_ms = now_ms
end
local elapsed = math.max(0, now_ms - last_ms)
tokens = math.min(capacity, tokens + elapsed * capacity / window_ms)
local allowed = 0
local retry_ms = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry_ms = math.ceil((1 - tokens) * window_ms / capacity)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'last_ms', tostring(now_ms))
redis.call('PEXPIRE', KEYS[1], window_ms * 2)
return {allowed, retry_ms}
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
            # v2 独立键类型，避免旧版 INCR 字符串键在滚动发布时触发 WRONGTYPE。
            key = f"{key}:tb2"
            allowed, retry_ms = await r.eval(
                _TOKEN_BUCKET, 1, key, limit, window * 1000
            )
            if not int(allowed):
                response = json_error_response(
                    request,
                    status_code=429,
                    code=429,
                    message="请求过于频繁，请稍后再试",
                )
                response.headers["Retry-After"] = str(max(1, (int(retry_ms) + 999) // 1000))
                return response
        except Exception as e:
            # 默认 fail-open：Redis 仅加速层，避免全站 503。
            # 敏感前缀可按 RATE_LIMIT_FAIL_CLOSED_PATHS 单独 fail-closed（503）。
            raw_paths = getattr(settings, "rate_limit_fail_closed_paths", "")
            prefixes = _fail_closed_prefixes(raw_paths if isinstance(raw_paths, str) else "")
            if path_requires_fail_closed(path, prefixes):
                _log_redis_failure(path, e, fail_closed=True)
                return json_error_response(
                    request,
                    status_code=503,
                    code=50301,
                    message="限流服务暂不可用，请稍后重试",
                )
            _log_redis_failure(path, e, fail_closed=False)

        return await call_next(request)

"""Webhook / 管理接口 IP 白名单"""
from __future__ import annotations

import ipaddress
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings

logger = logging.getLogger(__name__)

WEBHOOK_PATHS = (
    "/api/v1/payments/wechat/notify",
    "/api/v1/payments/wechat/refund-notify",
    "/api/v1/payments/alipay/notify",
    "/api/v1/delivery/meituan/notify",
    "/api/v1/delivery/eleme/notify",
)


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def _parse_allowlist(raw: str) -> list[ipaddress._BaseNetwork | ipaddress._BaseAddress]:
    items = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "/" in part:
                items.append(ipaddress.ip_network(part, strict=False))
            else:
                items.append(ipaddress.ip_address(part))
        except ValueError:
            logger.warning("忽略非法 IP 白名单项: %s", part)
    return items


def ip_allowed(ip: str, allowlist: list) -> bool:
    if not allowlist:
        return True  # 未配置则不限制
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for item in allowlist:
        if isinstance(item, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            if addr in item:
                return True
        elif addr == item:
            return True
    return False


class WebhookAllowlistMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not any(path.startswith(p) or path == p for p in WEBHOOK_PATHS):
            return await call_next(request)

        settings = get_settings()
        raw = getattr(settings, "webhook_ip_allowlist", "") or ""
        allowlist = _parse_allowlist(raw)
        if not allowlist:
            # 生产未配置白名单时告警但放行（避免误杀）；可用 WEBHOOK_REQUIRE_ALLOWLIST=true 强制
            if settings.is_production and getattr(settings, "webhook_require_allowlist", False):
                return JSONResponse(
                    status_code=403,
                    content={"code": 403, "message": "Webhook IP 白名单未配置", "data": None},
                )
            return await call_next(request)

        ip = _client_ip(request)
        if not ip_allowed(ip, allowlist):
            logger.warning("Webhook IP 拒绝 path=%s ip=%s", path, ip)
            return JSONResponse(
                status_code=403,
                content={"code": 403, "message": "来源 IP 不在白名单", "data": None},
            )
        return await call_next(request)

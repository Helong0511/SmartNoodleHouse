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


def client_ip(request: Request) -> str:
    """解析真实客户端 IP。

    仅当 request.client.host 属于 TRUSTED_PROXY_IPS 时，才信任
    X-Real-IP / X-Forwarded-For；否则直接使用直连 peer，防止客户端伪造绕过白名单与限流。
    """
    peer = request.client.host if request.client else ""
    settings = get_settings()
    from app.services.system_settings_service import SystemSettingsService

    trusted_raw = SystemSettingsService.resolved_str_sync(
        "trusted_proxy_ips", getattr(settings, "trusted_proxy_ips", "") or ""
    )
    trusted = _parse_allowlist(trusted_raw)
    # 未配置可信代理时：绝不信任客户端伪造头，只用 peer
    if not trusted or not peer or not ip_allowed(peer, trusted):
        return peer

    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        # 从右向左跳过可信代理，取第一个非可信 IP；
        # 最左段由客户端可伪造，直接采用会被绕过白名单与限流
        for part in reversed(parts):
            if not ip_allowed(part, trusted):
                return part
        # 整条链均为可信代理（异常场景）：回退最左段
        if parts:
            return parts[0]
    return peer


def _client_ip(request: Request) -> str:
    """兼容旧名。"""
    return client_ip(request)


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
        from app.services.system_settings_service import SystemSettingsService

        try:
            await SystemSettingsService.touch_cache()
        except Exception:
            pass

        path = request.url.path
        if not any(path.startswith(p) or path == p for p in WEBHOOK_PATHS):
            return await call_next(request)

        settings = get_settings()
        raw = SystemSettingsService.resolved_str_sync(
            "webhook_ip_allowlist", getattr(settings, "webhook_ip_allowlist", "") or ""
        )
        allowlist = _parse_allowlist(raw)
        if not allowlist:
            # 生产未配置白名单时告警但放行（避免误杀）；可用 WEBHOOK_REQUIRE_ALLOWLIST=true 强制
            require = SystemSettingsService.resolved_bool_sync(
                "webhook_require_allowlist",
                bool(getattr(settings, "webhook_require_allowlist", False)),
            )
            if settings.is_production and require:
                return JSONResponse(
                    status_code=403,
                    content={"code": 403, "message": "Webhook IP 白名单未配置", "data": None},
                )
            return await call_next(request)

        ip = client_ip(request)
        if not ip_allowed(ip, allowlist):
            logger.warning("Webhook IP 拒绝 path=%s ip=%s", path, ip)
            return JSONResponse(
                status_code=403,
                content={"code": 403, "message": "来源 IP 不在白名单", "data": None},
            )
        return await call_next(request)

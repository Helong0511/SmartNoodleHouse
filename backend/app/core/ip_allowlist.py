"""Webhook / 管理接口 IP 白名单"""
from __future__ import annotations

import ipaddress
import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.response import json_error_response

logger = logging.getLogger(__name__)

WEBHOOK_PATHS = (
    "/api/payments/wechat/notify",
    "/api/payments/wechat/refund-notify",
    "/api/payments/alipay/notify",
    "/api/delivery/meituan/notify",
    "/api/delivery/eleme/notify",
)


def _normalize_ip(value: str | None) -> str | None:
    """只接受合法 IP 字面量并统一压缩格式，拒绝端口、脏值和任意字符串。"""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return ipaddress.ip_address(raw).compressed
    except ValueError:
        return None


def client_ip(request: Request) -> str:
    """解析真实客户端 IP。

    仅当 request.client.host 属于 TRUSTED_PROXY_IPS 时，才信任
    X-Real-IP / X-Forwarded-For；否则直接使用直连 peer，防止客户端伪造绕过白名单与限流。
    """
    peer_raw = request.client.host if request.client else ""
    peer = _normalize_ip(peer_raw) or peer_raw
    settings = get_settings()
    from app.services.system_settings_service import SystemSettingsService

    trusted_raw = SystemSettingsService.resolved_str_sync(
        "trusted_proxy_ips", getattr(settings, "trusted_proxy_ips", "") or ""
    )
    trusted = _parse_allowlist(trusted_raw)
    # 未配置可信代理时：绝不信任客户端伪造头，只用 peer
    if not trusted or not peer or not ip_allowed(peer, trusted):
        return peer

    real = _normalize_ip(request.headers.get("x-real-ip"))
    if real:
        return real
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        # 从右向左跳过可信代理，取第一个非可信 IP；
        # 最左段由客户端可伪造，直接采用会被绕过白名单与限流
        for part in reversed(parts):
            candidate = _normalize_ip(part)
            if candidate and not ip_allowed(candidate, trusted):
                return candidate
        # 整条链均为可信代理或含非法项：回退最左侧第一个合法地址。
        for part in parts:
            candidate = _normalize_ip(part)
            if candidate:
                return candidate
    return peer


def _parse_allowlist(raw: str) -> list[ipaddress._BaseNetwork | ipaddress._BaseAddress]:
    items = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "/" in part:
                network = ipaddress.ip_network(part, strict=False)
                # /0 等价于信任全网；用于 trusted_proxy_ips 时会让任意客户端伪造转发头。
                if network.prefixlen == 0:
                    raise ValueError("全网段不允许作为 IP 白名单")
                items.append(network)
            else:
                items.append(ipaddress.ip_address(part))
        except ValueError:
            logger.debug("忽略非法 IP 白名单项: %s", part)
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
            # 生产必须 fail-closed：动态配置被清空或包含非法项时，不得静默放行支付/外卖回调。
            require = SystemSettingsService.resolved_bool_sync(
                "webhook_require_allowlist",
                bool(getattr(settings, "webhook_require_allowlist", False)),
            )
            if settings.is_production or require:
                logger.error("Webhook IP 白名单为空或无有效项，拒绝回调 path=%s", path)
                return json_error_response(
                    request,
                    status_code=403,
                    code=403,
                    message="Webhook IP 白名单未配置或无有效项",
                )
            return await call_next(request)

        ip = client_ip(request)
        if not ip_allowed(ip, allowlist):
            logger.warning("Webhook IP 拒绝 path=%s ip=%s", path, ip)
            return json_error_response(
                request,
                status_code=403,
                code=403,
                message="来源 IP 不在白名单",
            )
        return await call_next(request)

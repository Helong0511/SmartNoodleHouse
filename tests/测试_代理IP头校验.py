"""代理 IP 头必须解析为合法 IP，防止伪造值污染限流键。"""
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request

from app.core.ip_allowlist import _normalize_ip, client_ip


def _request(peer: str, headers: list[tuple[bytes, bytes]]) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "server": ("test", 80),
        "client": (peer, 12345),
    }
    return Request(scope)


def test_normalize_ip_rejects_arbitrary_values_and_normalizes_valid_ip():
    assert _normalize_ip(" 2001:0db8::1 ") == "2001:db8::1"
    assert _normalize_ip("203.0.113.7:8080") is None
    assert _normalize_ip("attacker-controlled-key") is None
    assert _normalize_ip("") is None


def test_untrusted_peer_cannot_supply_forwarded_ip():
    request = _request("198.51.100.10", [(b"x-forwarded-for", b"203.0.113.9")])
    with (
        patch("app.core.ip_allowlist.get_settings", return_value=SimpleNamespace(trusted_proxy_ips="10.0.0.1")),
        patch("app.services.system_settings_service.SystemSettingsService.resolved_str_sync", return_value="10.0.0.1"),
    ):
        assert client_ip(request) == "198.51.100.10"


def test_trusted_proxy_invalid_x_real_ip_falls_back_to_peer():
    request = _request("10.0.0.1", [(b"x-real-ip", b"not-an-ip")])
    with (
        patch("app.core.ip_allowlist.get_settings", return_value=SimpleNamespace(trusted_proxy_ips="10.0.0.1")),
        patch("app.services.system_settings_service.SystemSettingsService.resolved_str_sync", return_value="10.0.0.1"),
    ):
        assert client_ip(request) == "10.0.0.1"


def test_trusted_proxy_forwarded_chain_ignores_invalid_values():
    request = _request(
        "10.0.0.1",
        [(b"x-forwarded-for", b"not-an-ip, 203.0.113.8, 10.0.0.2")],
    )
    with (
        patch("app.core.ip_allowlist.get_settings", return_value=SimpleNamespace(trusted_proxy_ips="10.0.0.1,10.0.0.2")),
        patch("app.services.system_settings_service.SystemSettingsService.resolved_str_sync", return_value="10.0.0.1,10.0.0.2"),
    ):
        assert client_ip(request) == "203.0.113.8"

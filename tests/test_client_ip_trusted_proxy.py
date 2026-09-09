"""可信代理：非白名单 peer 时伪造 X-Real-IP / X-Forwarded-For 无效。"""
from unittest.mock import MagicMock, patch

from app.core.ip_allowlist import client_ip


def _req(*, real_ip=None, xff=None, host="203.0.113.50"):
    headers = {}
    if real_ip is not None:
        headers["x-real-ip"] = real_ip
    if xff is not None:
        headers["x-forwarded-for"] = xff

    def get_header(name, default=None):
        key = name.lower()
        for hk, hv in headers.items():
            if hk.lower() == key:
                return hv
        return default

    request = MagicMock()
    request.headers.get = get_header
    request.client = MagicMock()
    request.client.host = host
    return request


def test_untrusted_peer_ignores_forged_x_real_ip():
    settings = MagicMock()
    settings.trusted_proxy_ips = "127.0.0.1,::1"
    with patch("app.core.ip_allowlist.get_settings", return_value=settings):
        ip = client_ip(_req(real_ip="1.2.3.4", host="198.51.100.9"))
    assert ip == "198.51.100.9"


def test_untrusted_peer_ignores_forged_xff():
    settings = MagicMock()
    settings.trusted_proxy_ips = "127.0.0.1"
    with patch("app.core.ip_allowlist.get_settings", return_value=settings):
        ip = client_ip(_req(xff="8.8.8.8, 9.9.9.9", host="198.51.100.9"))
    assert ip == "198.51.100.9"


def test_trusted_proxy_accepts_x_real_ip():
    settings = MagicMock()
    settings.trusted_proxy_ips = "10.0.0.0/8,127.0.0.1"
    with patch("app.core.ip_allowlist.get_settings", return_value=settings):
        ip = client_ip(_req(real_ip="203.0.113.9", host="10.1.2.3"))
    assert ip == "203.0.113.9"


def test_empty_trusted_proxy_never_trusts_headers():
    settings = MagicMock()
    settings.trusted_proxy_ips = ""
    with patch("app.core.ip_allowlist.get_settings", return_value=settings):
        ip = client_ip(_req(real_ip="1.1.1.1", host="192.0.2.1"))
    assert ip == "192.0.2.1"

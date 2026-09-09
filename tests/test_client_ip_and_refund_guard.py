"""客户端 IP 解析与生产打印 Mock 硬拒绝。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.ip_allowlist import client_ip
from app.core.startup_checks import validate_settings


def _req(*, real_ip=None, xff=None, host="10.0.0.1"):
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


def test_client_ip_prefers_x_real_ip_over_forged_xff(monkeypatch):
    # peer 必须是可信代理，才会解析 X-Real-IP
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "10.0.0.1")
    from app.core.config import get_settings

    get_settings.cache_clear()
    ip = client_ip(_req(real_ip="203.0.113.9", xff="1.2.3.4, 203.0.113.9", host="10.0.0.1"))
    assert ip == "203.0.113.9"
    get_settings.cache_clear()


def test_client_ip_uses_xff_first_when_trusted_proxy(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "10.0.0.1")
    from app.core.config import get_settings

    get_settings.cache_clear()
    assert client_ip(_req(xff="5.6.7.8, 10.0.0.1", host="10.0.0.1")) == "5.6.7.8"
    get_settings.cache_clear()


def test_client_ip_ignores_headers_from_untrusted_peer(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1")
    from app.core.config import get_settings

    get_settings.cache_clear()
    # 直连伪造头无效
    assert client_ip(_req(real_ip="203.0.113.9", xff="1.2.3.4", host="8.8.8.8")) == "8.8.8.8"
    get_settings.cache_clear()


def test_client_ip_falls_back_to_peer():
    assert client_ip(_req(host="127.0.0.1")) == "127.0.0.1"


def test_prod_rejects_allow_print_mock():
    s = SimpleNamespace(
        is_production=True,
        app_env="production",
        jwt_secret="K9mP2xQ7vL4nR8wT1yU5zA3bC6dE0fG2hJ4k",
        face_encryption_key="F7nB3qW9sE2mY6tR1uI5oP8aL4xC0vN2zM",
        app_debug=False,
        allow_payment_mock=False,
        allow_auth_mock=False,
        allow_delivery_mock=False,
        allow_print_mock=True,
        storage_backend="s3",
        s3_endpoint="https://s3.example.com",
        s3_access_key="ak",
        s3_secret_key="sk",
        s3_bucket="b",
        webhook_require_allowlist=False,
        webhook_ip_allowlist="203.0.113.10",
        display_screen_token="tok",
        process_role="leader",
        cors_origins="https://order.example.com",
        h5_public_base_url="https://order.example.com",
        secret_encryption_key="S7nB3qW9sE2mY6tR1uI5oP8aL4xC0vN2zM",
        enable_rate_limit=True,
        allow_opencv_face_fallback=False,
        wechat_mch_id="m",
        wechat_api_v3_key="k" * 32,
        wechat_cert_serial_no="s",
        wechat_app_id="wx",
        wechat_platform_public_key="-----BEGIN PUBLIC KEY-----\nMFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBAFake\n-----END PUBLIC KEY-----",
        wechat_platform_cert_path="",
        alipay_app_id="",
        alipay_private_key="",
        alipay_public_key="",
        xpyun_enabled=False,
        xpyun_user="",
        xpyun_user_key="",
        xpyun_printer_sn="",
    )
    with pytest.raises(SystemExit):
        validate_settings(s)

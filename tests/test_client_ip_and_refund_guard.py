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


def test_client_ip_prefers_x_real_ip_over_forged_xff():
    ip = client_ip(_req(real_ip="203.0.113.9", xff="1.2.3.4, 203.0.113.9"))
    assert ip == "203.0.113.9"


def test_client_ip_uses_xff_last_when_no_real_ip():
    assert client_ip(_req(xff="1.2.3.4, 5.6.7.8")) == "5.6.7.8"


def test_client_ip_falls_back_to_peer():
    assert client_ip(_req(host="127.0.0.1")) == "127.0.0.1"


def test_prod_rejects_allow_print_mock():
    s = SimpleNamespace(
        is_production=True,
        jwt_secret="a" * 40,
        face_encryption_key="b" * 40,
        app_debug=False,
        allow_payment_mock=False,
        allow_auth_mock=False,
        allow_delivery_mock=False,
        allow_print_mock=True,
        storage_backend="s3",
        webhook_require_allowlist=False,
        webhook_ip_allowlist="",
        process_role="leader",
        wechat_mch_id="m",
        wechat_api_v3_key="k" * 32,
        wechat_cert_serial_no="s",
        wechat_app_id="wx",
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

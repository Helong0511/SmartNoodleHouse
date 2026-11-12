"""Regression tests for strict payment callback identity binding.

These tests use generated ephemeral RSA keys and never contact payment providers.
"""
import base64
import json
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def _alipay_signed_body(params: dict, private_key) -> bytes:
    from app.services.payment_verify import AlipayVerifier

    content = AlipayVerifier._build_sign_content(params)
    signature = private_key.sign(
        content.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    signed = {**params, "sign_type": "RSA2", "sign": base64.b64encode(signature).decode("ascii")}
    return json.dumps(signed).encode("utf-8")


def _rsa_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private_key, public_pem


def test_wechat_callback_rejects_unbound_platform_key_serial(monkeypatch):
    from app.services import payment_verify

    settings = SimpleNamespace(
        wechat_api_v3_key="x" * 32,
        wechat_mch_id="merchant-1",
        wechat_platform_public_key="unused-public-key",
        wechat_platform_cert_path="",
        wechat_platform_serial_no="EXPECTED-SERIAL",
    )
    monkeypatch.setattr(payment_verify, "get_settings", lambda: settings)

    headers = {
        "Wechatpay-Signature": base64.b64encode(b"not-a-signature").decode("ascii"),
        "Wechatpay-Timestamp": str(int(time.time())),
        "Wechatpay-Nonce": "nonce",
        "Wechatpay-Serial": "UNEXPECTED-SERIAL",
    }
    with pytest.raises(ValueError, match="序列号不匹配"):
        payment_verify.WechatPayVerifier().verify_and_parse(headers, b"{}")


def test_alipay_production_callback_rejects_mismatched_app_id(monkeypatch):
    from app.services import payment_verify

    private_key, public_pem = _rsa_pair()
    settings = SimpleNamespace(
        alipay_public_key=public_pem,
        alipay_app_id="expected-app",
        alipay_seller_id="expected-seller",
        alipay_seller_email="",
        is_production=True,
    )
    monkeypatch.setattr(payment_verify, "get_settings", lambda: settings)
    body = _alipay_signed_body(
        {
            "app_id": "other-app",
            "seller_id": "expected-seller",
            "out_trade_no": "payment-1",
            "trade_no": "provider-1",
            "trade_status": "TRADE_SUCCESS",
            "total_amount": "12.00",
        },
        private_key,
    )

    with pytest.raises(ValueError, match="app_id 不匹配"):
        payment_verify.AlipayVerifier().verify_and_parse({}, body)


def test_alipay_production_callback_rejects_missing_seller_identity(monkeypatch):
    from app.services import payment_verify

    private_key, public_pem = _rsa_pair()
    settings = SimpleNamespace(
        alipay_public_key=public_pem,
        alipay_app_id="expected-app",
        alipay_seller_id="expected-seller",
        alipay_seller_email="",
        is_production=True,
    )
    monkeypatch.setattr(payment_verify, "get_settings", lambda: settings)
    body = _alipay_signed_body(
        {
            "app_id": "expected-app",
            "out_trade_no": "payment-1",
            "trade_no": "provider-1",
            "trade_status": "TRADE_SUCCESS",
            "total_amount": "12.00",
        },
        private_key,
    )

    with pytest.raises(ValueError, match="缺少或未匹配已配置的卖家身份"):
        payment_verify.AlipayVerifier().verify_and_parse({}, body)

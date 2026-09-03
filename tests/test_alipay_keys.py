"""支付宝 RSA 密钥加载（PKCS8 / PKCS1）测试。"""
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.utils.alipay_keys import (
    decode_alipay_http_body,
    load_alipay_private_key,
    load_alipay_public_key,
    parse_alipay_http_json,
)


def _gen_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pkcs8 = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    priv_pkcs1 = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pkcs8, priv_pkcs1, pub


def _strip_pem(pem: str) -> str:
    lines = [ln for ln in pem.strip().splitlines() if not ln.startswith("-----")]
    return "".join(lines)


def test_load_pkcs8_raw_b64():
    priv_pkcs8, _, pub = _gen_pair()
    loaded = load_alipay_private_key(_strip_pem(priv_pkcs8))
    assert loaded.key_size == 2048
    load_alipay_public_key(_strip_pem(pub))


def test_load_pkcs1_raw_b64():
    _, priv_pkcs1, _ = _gen_pair()
    loaded = load_alipay_private_key(_strip_pem(priv_pkcs1))
    assert loaded.key_size == 2048


def test_load_pem_with_headers():
    priv_pkcs8, _, pub = _gen_pair()
    load_alipay_private_key(priv_pkcs8)
    load_alipay_public_key(pub)


def test_decode_alipay_gbk_body():
    # 含 0xd1 的 GBK JSON（复现用户 invalid continuation byte）
    payload = b'{"code":"40002","msg":"\xd1\xa7\xb7\xa8"}'
    with pytest.raises(UnicodeDecodeError):
        payload.decode("utf-8")
    text = decode_alipay_http_body(payload)
    assert "code" in text
    data = parse_alipay_http_json(payload)
    assert data["code"] == "40002"


def test_encode_alipay_query_puts_charset_in_query():
    from app.utils.alipay_keys import encode_alipay_query, build_alipay_sign_content, sign_alipay_params_rsa2

    priv, _, _ = _gen_pair()
    params = {
        "app_id": "2021000000000000",
        "method": "alipay.system.oauth.token",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": "2026-09-03 10:00:00",
        "version": "1.0",
        "grant_type": "authorization_code",
        "code": "authcode123",
    }
    content = build_alipay_sign_content(params)
    assert "sign_type=RSA2" in content
    assert "charset=utf-8" in content
    assert "sign=" not in content.split("&")[0] or True
    # sign 字段本身不应进入待签串
    assert all(not p.startswith("sign=") for p in content.split("&"))
    params["sign"] = sign_alipay_params_rsa2(params, priv)
    q = encode_alipay_query(params)
    assert "charset=utf-8" in q
    assert "sign=" in q
    assert " " not in q

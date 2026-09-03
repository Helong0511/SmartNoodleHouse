"""支付回调签名验证 - 微信 V3 / 支付宝 RSA2（fail-closed）"""
from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 回调时间戳允许偏差（秒）
_WECHAT_TS_SKEW = 300


def _dev_mode_allowed() -> bool:
    s = get_settings()
    return s.is_development and s.app_debug and s.allow_payment_mock


def _load_pem(key_str: str, is_private: bool = False):
    from app.utils.alipay_keys import load_alipay_private_key, load_alipay_public_key

    if is_private:
        return load_alipay_private_key(key_str)
    return load_alipay_public_key(key_str)


def _load_wechat_platform_public_key() -> Any:
    settings = get_settings()
    pem = (settings.wechat_platform_public_key or "").strip()
    if not pem:
        path = Path(settings.wechat_platform_cert_path)
        if path.is_file():
            pem = path.read_text(encoding="utf-8")
    if not pem:
        raise ValueError("未配置微信平台公钥（WECHAT_PLATFORM_PUBLIC_KEY 或证书文件），拒绝回调")
    # 证书 PEM 需提取公钥
    if "BEGIN CERTIFICATE" in pem:
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(pem.encode())
        return cert.public_key()
    return _load_pem(pem, is_private=False)


class WechatPayVerifier:
    """微信支付 APIv3 回调：必须验签后再解密"""

    def verify_and_parse(self, headers: dict, body: bytes) -> dict[str, Any]:
        settings = get_settings()
        body_str = body.decode("utf-8")
        data = json.loads(body_str)

        if not settings.wechat_api_v3_key or not settings.wechat_mch_id:
            if _dev_mode_allowed() and data.get("_dev_simulate"):
                logger.warning("微信支付开发模式：跳过验签（ALLOW_PAYMENT_MOCK）")
                return data
            raise ValueError("微信支付未配置，拒绝回调")

        sig = headers.get("wechatpay-signature") or headers.get("Wechatpay-Signature")
        ts = headers.get("wechatpay-timestamp") or headers.get("Wechatpay-Timestamp")
        nonce = headers.get("wechatpay-nonce") or headers.get("Wechatpay-Nonce")
        serial = headers.get("wechatpay-serial") or headers.get("Wechatpay-Serial")

        if not all([sig, ts, nonce]):
            raise ValueError("微信回调缺少签名头")

        try:
            ts_int = int(ts)
        except ValueError as e:
            raise ValueError("微信回调时间戳非法") from e
        if abs(int(time.time()) - ts_int) > _WECHAT_TS_SKEW:
            raise ValueError("微信回调时间戳过期（疑似重放）")

        message = f"{ts}\n{nonce}\n{body_str}\n".encode("utf-8")
        pub_key = _load_wechat_platform_public_key()
        try:
            pub_key.verify(
                base64.b64decode(sig),
                message,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as e:
            logger.error("微信平台签名验证失败 serial=%s: %s", serial, e)
            raise ValueError("微信回调签名验证失败") from e

        resource = data.get("resource")
        if not resource or not resource.get("ciphertext"):
            raise ValueError("微信回调缺少加密 resource，拒绝明文回调")

        plain = self._decrypt_resource(resource, settings.wechat_api_v3_key)
        inner = json.loads(plain)
        # 可选：校验商户号
        mch = inner.get("mchid") or inner.get("mch_id")
        if mch and str(mch) != str(settings.wechat_mch_id):
            raise ValueError("微信回调商户号不匹配")
        event_type = str(data.get("event_type") or "")
        return {
            "event_type": event_type,
            "out_trade_no": inner.get("out_trade_no"),
            "transaction_id": inner.get("transaction_id"),
            "trade_state": inner.get("trade_state"),
            "out_refund_no": inner.get("out_refund_no"),
            "refund_id": inner.get("refund_id"),
            "refund_status": inner.get("refund_status"),
            "amount": inner.get("amount", {}),
            "mchid": mch,
            "success_time": inner.get("success_time"),
        }

    @staticmethod
    def _decrypt_resource(resource: dict, api_v3_key: str) -> str:
        key = api_v3_key.encode("utf-8")
        nonce = resource["nonce"].encode("utf-8")
        ad = resource.get("associated_data", "").encode("utf-8")
        ciphertext = base64.b64decode(resource["ciphertext"])
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, ad).decode("utf-8")


class AlipayVerifier:
    """支付宝 RSA2 回调验签"""

    def verify_and_parse(self, headers: dict, body: bytes) -> dict[str, Any]:
        settings = get_settings()
        body_str = body.decode("utf-8")

        from urllib.parse import parse_qs

        if body_str.startswith("{"):
            params = json.loads(body_str)
        else:
            parsed = parse_qs(body_str, keep_blank_values=True)
            params = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}

        if not settings.alipay_public_key:
            if _dev_mode_allowed() and params.get("_dev_simulate"):
                logger.warning("支付宝开发模式：跳过验签（ALLOW_PAYMENT_MOCK）")
                return params
            raise ValueError("支付宝未配置，拒绝回调")

        sign = params.pop("sign", None)
        params.pop("sign_type", None)
        if not sign:
            raise ValueError("支付宝回调缺少 sign")

        content = self._build_sign_content(params)
        if not self._verify_rsa2(content, sign, settings.alipay_public_key):
            raise ValueError("支付宝签名验证失败")

        # 校验 app_id
        app_id = params.get("app_id")
        if app_id and settings.alipay_app_id and str(app_id) != str(settings.alipay_app_id):
            raise ValueError("支付宝回调 app_id 不匹配")

        return params

    @staticmethod
    def _build_sign_content(params: dict) -> str:
        items = sorted((k, v) for k, v in params.items() if v is not None and v != "" and k not in ("sign", "sign_type"))
        return "&".join(f"{k}={v}" for k, v in items)

    @staticmethod
    def _verify_rsa2(content: str, sign_b64: str, public_key_pem: str) -> bool:
        try:
            pub_key = _load_pem(public_key_pem, is_private=False)
            signature = base64.b64decode(sign_b64)
            pub_key.verify(
                signature,
                content.encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except Exception as e:
            logger.error("支付宝验签失败: %s", e)
            return False

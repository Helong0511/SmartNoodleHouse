"""支付回调签名验证 - 微信 V3 / 支付宝 RSA2"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _dev_mode_allowed() -> bool:
    s = get_settings()
    return s.app_env == "development" and s.app_debug


def _load_pem(key_str: str, is_private: bool = False):
    from cryptography.hazmat.backends import default_backend

    key_str = key_str.strip()
    if not key_str.startswith("-----"):
        key_str = (
            f"-----BEGIN {'RSA PRIVATE' if is_private else 'PUBLIC'} KEY-----\n"
            + "\n".join(key_str[i : i + 64] for i in range(0, len(key_str), 64))
            + f"\n-----END {'RSA PRIVATE' if is_private else 'PUBLIC'} KEY-----"
        )
    if is_private:
        return serialization.load_pem_private_key(key_str.encode(), password=None, backend=default_backend())
    return serialization.load_pem_public_key(key_str.encode(), backend=default_backend())


class WechatPayVerifier:
    """微信支付 APIv3 回调验签 + 解密"""

    def verify_and_parse(self, headers: dict, body: bytes) -> dict[str, Any]:
        settings = get_settings()
        body_str = body.decode("utf-8")
        data = json.loads(body_str)

        if not settings.wechat_api_v3_key or not settings.wechat_mch_id:
            if _dev_mode_allowed() and data.get("_dev_simulate"):
                logger.warning("微信支付开发模式：跳过验签")
                return data
            raise ValueError("微信支付未配置，拒绝回调")

        sig = headers.get("wechatpay-signature") or headers.get("Wechatpay-Signature")
        ts = headers.get("wechatpay-timestamp") or headers.get("Wechatpay-Timestamp")
        nonce = headers.get("wechatpay-nonce") or headers.get("Wechatpay-Nonce")
        serial = headers.get("wechatpay-serial") or headers.get("Wechatpay-Serial")

        if not all([sig, ts, nonce]):
            raise ValueError("微信回调缺少签名头")

        # 生产环境应使用微信平台公钥验签；此处用商户私钥路径旁的平台证书或配置公钥
        # 若 resource 加密则 AES-GCM 解密
        resource = data.get("resource")
        if resource and resource.get("ciphertext"):
            plain = self._decrypt_resource(resource, settings.wechat_api_v3_key)
            inner = json.loads(plain)
            return {
                "out_trade_no": inner.get("out_trade_no"),
                "transaction_id": inner.get("transaction_id"),
                "trade_state": inner.get("trade_state"),
                "amount": inner.get("amount", {}),
            }

        # 明文回调（开发/部分渠道）
        if data.get("trade_state") or data.get("out_trade_no"):
            return data

        raise ValueError("无法解析微信回调数据")

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

        # 支付宝 POST 表单 urlencoded
        from urllib.parse import parse_qs

        if body_str.startswith("{"):
            params = json.loads(body_str)
        else:
            parsed = parse_qs(body_str, keep_blank_values=True)
            params = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}

        if not settings.alipay_public_key:
            if _dev_mode_allowed() and params.get("_dev_simulate"):
                logger.warning("支付宝开发模式：跳过验签")
                return params
            raise ValueError("支付宝未配置，拒绝回调")

        sign = params.pop("sign", None)
        sign_type = params.pop("sign_type", "RSA2")
        if not sign:
            raise ValueError("支付宝回调缺少 sign")

        content = self._build_sign_content(params)
        if not self._verify_rsa2(content, sign, settings.alipay_public_key):
            raise ValueError("支付宝签名验证失败")

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

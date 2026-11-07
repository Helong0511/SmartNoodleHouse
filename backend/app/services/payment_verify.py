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

from app.core.config import dev_mode_allowed, get_settings

logger = logging.getLogger(__name__)

# 回调时间戳允许偏差（秒）
_WECHAT_TS_SKEW = 300


def _dev_mode_allowed() -> bool:
    return dev_mode_allowed("allow_payment_mock")


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

        if not all([sig, ts, nonce, serial]):
            raise ValueError("微信回调缺少签名头或平台证书序列号")

        try:
            ts_int = int(ts)
        except ValueError as e:
            raise ValueError("微信回调时间戳非法") from e
        if abs(int(time.time()) - ts_int) > _WECHAT_TS_SKEW:
            raise ValueError("微信回调时间戳过期（疑似重放）")

        message = f"{ts}\n{nonce}\n{body_str}\n".encode("utf-8")
        # 使用证书文件验签时，同时校验证书序列号，避免仅用任意本地证书公钥验签。
        # 若配置的是独立平台公钥 PEM，则 serial 仍必须存在，但公钥本身不携带证书序列号。
        settings_cert = (settings.wechat_platform_public_key or "").strip()
        if not settings_cert:
            cert_path = Path(settings.wechat_platform_cert_path)
            if cert_path.is_file():
                settings_cert = cert_path.read_text(encoding="utf-8")
        received_serial = str(serial).strip()
        if "BEGIN CERTIFICATE" in settings_cert:
            from cryptography import x509

            platform_cert = x509.load_pem_x509_certificate(settings_cert.encode("utf-8"))
            expected_serial = format(platform_cert.serial_number, "X").lstrip("0") or "0"
            normalized_received = received_serial.upper().replace(":", "").lstrip("0") or "0"
            if normalized_received != expected_serial:
                raise ValueError("微信回调平台证书序列号不匹配")
        else:
            # 独立平台公钥 PEM 不含证书序列号，必须显式绑定配置的 serial，
            # 否则无法确认请求头 serial 与本地验签公钥属于同一平台密钥。
            expected_serial = str(getattr(settings, "wechat_platform_serial_no", "") or "").strip()
            if not expected_serial:
                raise ValueError("使用微信平台公钥 PEM 时必须配置 WECHAT_PLATFORM_SERIAL_NO")
            if received_serial != expected_serial:
                raise ValueError("微信回调平台公钥序列号不匹配")

        pub_key = _load_wechat_platform_public_key()
        try:
            pub_key.verify(
                base64.b64decode(sig, validate=True),
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
        # 商户号必须存在且必须匹配；缺失时不能把回调归属猜测为当前商户。
        mch = inner.get("mchid") or inner.get("mch_id")
        if not mch:
            raise ValueError("微信回调资源缺少商户号")
        if str(mch) != str(settings.wechat_mch_id):
            raise ValueError("微信回调商户号不匹配")

        event_type = str(data.get("event_type") or "").strip().upper()
        # 交易成功事件必须带齐商户订单号、渠道交易号和有效金额。
        # 微信金额单位为分；不接受缺失、零/负数或非 CNY 币种。
        if event_type == "TRANSACTION.SUCCESS":
            # 普通商户交易通知必须绑定下单 AppID，避免同一商户号下其他应用的交易进入本系统。
            callback_appid = str(inner.get("appid") or "").strip()
            expected_appid = str(settings.wechat_app_id or "").strip()
            if not callback_appid:
                raise ValueError("微信支付成功回调缺少 appid")
            if expected_appid and callback_appid != expected_appid:
                raise ValueError("微信支付成功回调 appid 不匹配")
            if not inner.get("out_trade_no") or not inner.get("transaction_id"):
                raise ValueError("微信支付成功回调缺少商户订单号或渠道交易号")
            amount = inner.get("amount")
            if not isinstance(amount, dict):
                raise ValueError("微信支付成功回调缺少金额对象")
            total_raw = amount.get("total")
            # 微信 APIv3 的 total 单位为分且 JSON 类型为整数；拒绝 bool、浮点数
            # 和字符串，避免 int() 静默截断或把 True 转成 1 分。
            if isinstance(total_raw, bool) or not isinstance(total_raw, int):
                raise ValueError("微信支付成功回调金额必须为整数分")
            total_fen = total_raw
            if total_fen <= 0:
                raise ValueError("微信支付成功回调金额必须大于零")
            currency_raw = amount.get("currency")
            if not isinstance(currency_raw, str) or currency_raw.strip().upper() != "CNY":
                raise ValueError("微信支付成功回调必须明确提供 CNY 币种")
            if str(inner.get("trade_state") or "").upper() != "SUCCESS":
                raise ValueError("微信 TRANSACTION.SUCCESS 事件的交易状态不是 SUCCESS")
        elif event_type in ("REFUND.SUCCESS", "REFUND.ABNORMAL"):
            # 退款通知同样必须绑定原支付和单笔退款；amount.total/refund 均为整数分。
            if not inner.get("out_trade_no") or not inner.get("transaction_id"):
                raise ValueError("微信退款回调缺少原支付单号或渠道交易号")
            if not inner.get("out_refund_no") or not inner.get("refund_id"):
                raise ValueError("微信退款回调缺少商户退款单号或渠道退款号")
            refund_status = str(inner.get("refund_status") or "").strip().upper()
            expected_refund_status = "SUCCESS" if event_type == "REFUND.SUCCESS" else "ABNORMAL"
            if refund_status != expected_refund_status:
                raise ValueError("微信退款事件类型与退款状态不匹配")
            amount = inner.get("amount")
            if not isinstance(amount, dict):
                raise ValueError("微信退款回调缺少金额对象")
            total_raw = amount.get("total")
            refund_raw = amount.get("refund")
            if (
                isinstance(total_raw, bool)
                or not isinstance(total_raw, int)
                or isinstance(refund_raw, bool)
                or not isinstance(refund_raw, int)
            ):
                raise ValueError("微信退款回调金额必须为整数分")
            if total_raw <= 0 or refund_raw <= 0 or refund_raw > total_raw:
                raise ValueError("微信退款回调金额范围非法")
            currency_raw = amount.get("currency")
            if not isinstance(currency_raw, str) or currency_raw.strip().upper() != "CNY":
                raise ValueError("微信退款回调必须明确提供 CNY 币种")
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
        sign_type = str(params.pop("sign_type", "") or "").strip().upper()
        if not sign:
            raise ValueError("支付宝回调缺少 sign")
        if sign_type != "RSA2":
            raise ValueError("支付宝回调 sign_type 必须为 RSA2")

        content = self._build_sign_content(params)
        if not self._verify_rsa2(content, sign, settings.alipay_public_key):
            raise ValueError("支付宝签名验证失败")

        # 生产环境必须配置本应用 app_id；只检查回调字段存在但不绑定本地配置，
        # 会允许其他应用签名的通知进入当前商户处理链。
        expected_app_id = str(getattr(settings, "alipay_app_id", "") or "").strip()
        if getattr(settings, "is_production", False) and not expected_app_id:
            raise ValueError("生产环境未配置 ALIPAY_APP_ID，拒绝处理支付宝回调")
        app_id = str(params.get("app_id") or "").strip()
        if not app_id:
            raise ValueError("支付宝回调缺少 app_id")
        if expected_app_id and app_id != expected_app_id:
            raise ValueError("支付宝回调 app_id 不匹配")

        # app_id 绑定应用；seller_id / seller_email 进一步绑定实际收款卖家账户。
        expected_seller_id = str(getattr(settings, "alipay_seller_id", "") or "").strip()
        expected_seller_email = str(getattr(settings, "alipay_seller_email", "") or "").strip()
        if getattr(settings, "is_production", False) and not (
            expected_seller_id or expected_seller_email
        ):
            raise ValueError(
                "生产环境必须配置 ALIPAY_SELLER_ID 或 ALIPAY_SELLER_EMAIL，拒绝未绑定卖家身份的回调"
            )
        callback_seller_id = str(params.get("seller_id") or "").strip()
        callback_seller_email = str(params.get("seller_email") or "").strip()
        if expected_seller_id and callback_seller_id and callback_seller_id != expected_seller_id:
            raise ValueError("支付宝回调 seller_id 不匹配")
        if expected_seller_email and callback_seller_email and callback_seller_email.lower() != expected_seller_email.lower():
            raise ValueError("支付宝回调 seller_email 不匹配")
        seller_matched = bool(
            (expected_seller_id and callback_seller_id == expected_seller_id)
            or (
                expected_seller_email
                and callback_seller_email.lower() == expected_seller_email.lower()
            )
        )
        if getattr(settings, "is_production", False) and not seller_matched:
            raise ValueError("支付宝回调缺少或未匹配已配置的卖家身份")

        return params

    @staticmethod
    def _build_sign_content(params: dict) -> str:
        items = sorted((k, v) for k, v in params.items() if v is not None and v != "" and k not in ("sign", "sign_type"))
        return "&".join(f"{k}={v}" for k, v in items)

    @staticmethod
    def _verify_rsa2(content: str, sign_b64: str, public_key_pem: str) -> bool:
        try:
            pub_key = _load_pem(public_key_pem, is_private=False)
            signature = base64.b64decode(sign_b64, validate=True)
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

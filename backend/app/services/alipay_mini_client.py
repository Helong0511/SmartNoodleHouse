"""支付宝小程序：手机号密文解密、订阅消息发送等。"""
from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.core.config import get_settings
from app.core.exceptions import AppException

logger = logging.getLogger(__name__)

_OK_CODES = frozenset({"", "10000", "200"})


def _mock_allowed() -> bool:
    s = get_settings()
    return s.is_development or bool(getattr(s, "allow_auth_mock", False))


def _alipay_mini_configured() -> bool:
    s = get_settings()
    return bool(s.alipay_app_id and s.alipay_private_key and s.alipay_public_key)


def _raise_order_ready_transport_error(exc: Exception) -> None:
    """连接建立前失败可安全重试；请求发出后响应未知时必须人工核实。"""
    import httpx

    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        raise exc
    from app.services.outbox_worker import ExternalSideEffectUncertain

    raise ExternalSideEffectUncertain(
        f"ALIPAY order-ready notification result unknown: {type(exc).__name__}"
    ) from exc


async def _templatemessage_gateway(biz: dict[str, Any]) -> dict[str, Any]:
    """调用 alipay.open.app.mini.templatemessage.send。"""
    from app.utils.alipay_keys import alipay_gateway_request, sign_alipay_params_rsa2

    settings = get_settings()
    method = "alipay.open.app.mini.templatemessage.send"
    params = {
        "app_id": settings.alipay_app_id,
        "method": method,
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "biz_content": json.dumps(biz, ensure_ascii=False, separators=(",", ":")),
    }
    params["sign"] = sign_alipay_params_rsa2(params, settings.alipay_private_key)
    data = await alipay_gateway_request(params, timeout=10.0)
    resp_key = method.replace(".", "_") + "_response"
    return data.get(resp_key) or data.get("error_response") or data


async def send_order_ready_subscribe(
    *,
    alipay_user_id: str,
    order_no: str,
    order_id: int,
    table_label: str | None = None,
) -> bool:
    """订单 READY：支付宝订阅消息（未配置模板则跳过；订阅类不传 form_id）。"""
    from app.services.system_settings_service import SystemSettingsService

    tmpl = SystemSettingsService.resolved_str_sync(
        "alipay_subscribe_template_order_ready",
        "",
    ).strip()
    user_id = (alipay_user_id or "").strip()
    if not tmpl or not user_id:
        return False
    if not _alipay_mini_configured():
        if _mock_allowed():
            import hashlib

            fp = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]
            logger.info("Mock 支付宝订阅 order_ready identity_fp=%s order=%s", fp, order_no)
            return True
        return False

    table_text = (table_label or "堂食").strip()[:20] or "堂食"
    # keyword 顺序须与开放平台模板一致；运营改模板后请同步此处占位
    data_obj = {
        "keyword1": {"value": (order_no or "-")[:32]},
        "keyword2": {"value": "餐品已备好，请取餐"},
        "keyword3": {"value": table_text},
    }
    biz = {
        "to_user_id": user_id,
        "user_template_id": tmpl,
        "page": f"packageOrder/result/result?order_id={order_id}",
        "data": json.dumps(data_obj, ensure_ascii=False, separators=(",", ":")),
    }
    try:
        body = await _templatemessage_gateway(biz)
    except Exception as e:
        logger.warning(
            "支付宝出餐订阅请求异常 order=%s error_type=%s",
            order_no,
            type(e).__name__,
        )
        _raise_order_ready_transport_error(e)
    if str(body.get("code") or "") == "10000":
        return True
    # 平台明确拒绝（例如用户未订阅/模板不匹配）是确定结果，不做自动重试。
    logger.warning("支付宝出餐订阅被平台拒绝 order=%s err=%s", order_no, body)
    return False


async def send_balance_low_subscribe(
    *,
    alipay_user_id: str,
    customer_id: int,
    balance: str,
    threshold: str,
) -> bool:
    """储值余额偏低：支付宝订阅消息。"""
    from app.services.system_settings_service import SystemSettingsService

    tmpl = SystemSettingsService.resolved_str_sync(
        "alipay_subscribe_template_balance_low",
        "",
    ).strip()
    user_id = (alipay_user_id or "").strip()
    if not tmpl or not user_id:
        return False
    if not _alipay_mini_configured():
        if _mock_allowed():
            logger.info(
                "Mock 支付宝订阅 balance_low customer=%s balance=%s",
                customer_id,
                balance,
            )
            return True
        return False

    data_obj = {
        "keyword1": {"value": f"{balance}元"},
        "keyword2": {"value": "储值余额偏低，欢迎到店充值"},
        "keyword3": {"value": f"{threshold}元"},
    }
    biz = {
        "to_user_id": user_id,
        "user_template_id": tmpl,
        "page": "pages/recharge/recharge",
        "data": json.dumps(data_obj, ensure_ascii=False, separators=(",", ":")),
    }
    try:
        body = await _templatemessage_gateway(biz)
        if str(body.get("code") or "") == "10000":
            return True
        logger.warning("支付宝余额提醒订阅失败 customer=%s err=%s", customer_id, body)
    except Exception as e:
        logger.warning("支付宝余额提醒订阅异常 customer=%s: %s", customer_id, e)
    return False


def _friendly_alipay_phone_error(msg: str) -> str:
    raw = str(msg or "").strip() or "获取失败"
    low = raw.lower()
    if (
        "insufficient permissions" in low
        or "insufficient-permission" in low
        or "isv.insufficient" in low
        or "权限不足" in raw
        or "没有权限" in raw
    ):
        return (
            "开放平台未开通「获取会员手机号」或能力未生效。"
            "请到 open.alipay.com → 小程序 → 能力列表申请该能力；"
            "联调期间可先手动输入手机号绑定"
        )
    if "40001" in raw or "缺少加密" in raw or "encrypt" in low:
        return "未配置接口内容加密：开放平台 → 开发设置 → 接口内容加密方式（AES）须与 .env 的 ALIPAY_AES_KEY 一致"
    return raw


def _decode_aes_key(raw: str) -> bytes:
    key = (raw or "").strip()
    if not key:
        raise AppException(40054, "未配置 ALIPAY_AES_KEY，无法解密支付宝手机号")
    try:
        decoded = base64.b64decode(key)
    except Exception as exc:
        raise AppException(40054, "ALIPAY_AES_KEY 格式无效") from exc
    if len(decoded) not in (16, 24, 32):
        raise AppException(40054, "ALIPAY_AES_KEY 长度无效")
    return decoded


def decrypt_alipay_content(encrypted_b64: str, aes_key: str | None = None) -> str:
    """解密支付宝接口内容加密（AES/CBC/PKCS7，IV 为 16 字节 0）。"""
    settings = get_settings()
    key = _decode_aes_key(aes_key or settings.alipay_aes_key)
    try:
        ciphertext = base64.b64decode((encrypted_b64 or "").strip())
    except Exception as exc:
        raise AppException(40054, "支付宝手机号密文无效") from exc
    if not ciphertext or len(ciphertext) % 16 != 0:
        raise AppException(40054, "支付宝手机号密文长度无效")
    try:
        iv = bytes(16)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
        return plain.decode("utf-8")
    except AppException:
        raise
    except Exception as exc:
        # 密钥与开放平台不一致、或误把整包 JSON 当密文时常见
        raise AppException(
            40054,
            "支付宝手机号解密失败，请确认 ALIPAY_AES_KEY 与开放平台「接口内容加密」一致",
        ) from exc


def _extract_mobile_from_dict(data: dict) -> str | None:
    code = str(data.get("code") or "")
    if code and code not in _OK_CODES:
        msg = data.get("msg") or data.get("subMsg") or data.get("message") or "获取失败"
        raise AppException(40054, f"支付宝手机号获取失败: {_friendly_alipay_phone_error(str(msg))}")
    mobile = data.get("mobile") or data.get("phoneNumber") or ""
    nested_data = data.get("data")
    if not mobile and isinstance(nested_data, dict):
        mobile = nested_data.get("mobile") or nested_data.get("phoneNumber") or ""
    mobile = str(mobile or "").strip()
    return mobile or None


def _extract_cipher_from_envelope(raw: str) -> tuple[str, str | None]:
    """
    解析 my.getPhoneNumber 的 res.response。

    返回 (cipher_text, plain_mobile)。
    官方完整报文形如：{"response":"<AES密文>","sign":"..."}；
    未配置加密时可能直接返回明文 JSON（含 code/mobile）。
    """
    text = (raw or "").strip()
    if not text.startswith("{"):
        return text, None
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        return text, None
    if not isinstance(envelope, dict):
        return text, None

    # 外层已是业务明文
    if "mobile" in envelope or "phoneNumber" in envelope or (
        "code" in envelope and "response" not in envelope
    ):
        return "", _extract_mobile_from_dict(envelope)

    nested = envelope.get("response")
    if isinstance(nested, dict):
        return "", _extract_mobile_from_dict(nested)
    if isinstance(nested, str) and nested.strip():
        inner = nested.strip()
        if inner.startswith("{"):
            try:
                inner_obj = json.loads(inner)
            except json.JSONDecodeError:
                return inner, None
            if isinstance(inner_obj, dict) and (
                "mobile" in inner_obj or "phoneNumber" in inner_obj or "code" in inner_obj
            ):
                return "", _extract_mobile_from_dict(inner_obj)
        return inner, None
    # 外层无 response 字段但含手机号字段
    mobile = _extract_mobile_from_dict(envelope)
    if mobile:
        return "", mobile
    return text, None


def _parse_plain_phone_payload(plain: str) -> str:
    try:
        data = json.loads(plain)
    except json.JSONDecodeError:
        phone = re.search(r"1\d{10}", plain)
        if phone:
            return phone.group(0)
        raise AppException(40054, "支付宝手机号解密结果无效")
    if isinstance(data, dict):
        mobile = _extract_mobile_from_dict(data)
        if mobile:
            return mobile
    phone = re.search(r"1\d{10}", plain)
    if phone:
        return phone.group(0)
    raise AppException(40054, "未能从支付宝响应中解析手机号")


def decrypt_phone_response(encrypted_b64: str) -> str:
    """my.getPhoneNumber 返回的 response 解密并提取手机号。"""
    raw = (encrypted_b64 or "").strip()
    if not raw:
        raise AppException(40054, "缺少支付宝手机号密文")

    settings = get_settings()
    if settings.is_development and not (settings.alipay_aes_key or "").strip():
        mock = re.search(r"1\d{10}", raw)
        if mock:
            logger.warning("开发环境未配置 ALIPAY_AES_KEY，从密文占位解析手机号（仅联调）")
            return mock.group(0)
        raise AppException(40054, "开发环境请配置 ALIPAY_AES_KEY 或改用手动输入手机号")

    cipher_text, plain_mobile = _extract_cipher_from_envelope(raw)
    if plain_mobile:
        # P0-3: 生产环境拒绝明文手机号（防客户端伪造），仅开发环境允许
        if not settings.is_development:
            raise AppException(40054, "支付宝手机号必须为 AES 加密报文，拒绝明文响应")
        return plain_mobile
    if not cipher_text:
        raise AppException(40054, "未能从支付宝响应中解析手机号")

    plain = decrypt_alipay_content(cipher_text)
    return _parse_plain_phone_payload(plain)

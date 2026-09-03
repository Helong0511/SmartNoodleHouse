"""支付宝小程序：手机号密文解密等。"""
from __future__ import annotations

import base64
import json
import logging
import re

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.core.config import get_settings
from app.core.exceptions import AppException

logger = logging.getLogger(__name__)

_OK_CODES = frozenset({"", "10000", "200"})


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
        return plain_mobile
    if not cipher_text:
        raise AppException(40054, "未能从支付宝响应中解析手机号")

    plain = decrypt_alipay_content(cipher_text)
    return _parse_plain_phone_payload(plain)

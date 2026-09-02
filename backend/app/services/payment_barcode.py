"""付款码识别：微信 / 支付宝当面付 auth_code 规则。"""
from __future__ import annotations

import re

from app.core.exceptions import AppException

# 微信：18 位数字，以 10～15 开头
_WECHAT_RE = re.compile(r"^(?:1[0-5])\d{16}$")
# 支付宝：16～24 位数字，以 25～30 开头
_ALIPAY_RE = re.compile(r"^(?:2[5-9]|30)\d{14,22}$")


def detect_barcode_provider(auth_code: str) -> str:
    """根据付款码识别支付渠道，返回 WECHAT 或 ALIPAY。"""
    code = (auth_code or "").strip()
    if not code:
        raise AppException(40041, "请扫描顾客付款码")
    if _WECHAT_RE.match(code):
        return "WECHAT"
    if _ALIPAY_RE.match(code):
        return "ALIPAY"
    raise AppException(40041, "无法识别付款码，请确认扫描的是微信或支付宝付款码")


def resolve_barcode_provider(auth_code: str, provider: str | None = None) -> str:
    """按付款码自动识别；无法识别时再使用客户端传入的 provider（兼容旧版）。"""
    code = (auth_code or "").strip()
    if _WECHAT_RE.match(code):
        return "WECHAT"
    if _ALIPAY_RE.match(code):
        return "ALIPAY"
    explicit = (provider or "").strip().upper()
    if explicit in ("WECHAT", "ALIPAY"):
        return explicit
    return detect_barcode_provider(code)

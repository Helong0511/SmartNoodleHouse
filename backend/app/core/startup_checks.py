"""生产启动校验：危险默认配置必须显式覆盖。"""
from __future__ import annotations

import logging
import sys

from app.core.config import Settings

logger = logging.getLogger(__name__)

_DANGEROUS_JWT = {"change-me", "secret", "jwt-secret", ""}
_DANGEROUS_FACE = {
    "change-me-face-encryption-key-32bytes!!",
    "change-me",
    "",
}


def validate_settings(settings: Settings) -> None:
    errors: list[str] = []
    warnings: list[str] = []
    is_prod = settings.is_production

    jwt = (settings.jwt_secret or "").strip()
    if jwt.lower() in _DANGEROUS_JWT or len(jwt) < 32:
        msg = "JWT_SECRET 过弱或为空，必须为至少 32 字符的随机串"
        (errors if is_prod else warnings).append(msg)

    face = (settings.face_encryption_key or "").strip()
    if face in _DANGEROUS_FACE or len(face) < 32:
        msg = "FACE_ENCRYPTION_KEY 为默认值/过短/空，必须更换"
        (errors if is_prod else warnings).append(msg)

    if is_prod and settings.app_debug:
        errors.append("生产环境禁止 APP_DEBUG=true")

    if is_prod and settings.allow_payment_mock:
        errors.append("生产环境禁止 ALLOW_PAYMENT_MOCK=true")

    if is_prod and settings.allow_auth_mock:
        errors.append("生产环境禁止 ALLOW_AUTH_MOCK=true")

    if is_prod and settings.allow_delivery_mock:
        errors.append("生产环境禁止 ALLOW_DELIVERY_MOCK=true")

    if is_prod and settings.allow_print_mock:
        errors.append("生产环境禁止 ALLOW_PRINT_MOCK=true（会导致厨房以为已出票实际未打印）")

    if is_prod and (settings.storage_backend or "local").lower() == "local":
        warnings.append("生产环境建议 STORAGE_BACKEND=s3，避免本地 uploads 直出")

    if is_prod and settings.webhook_require_allowlist and not (settings.webhook_ip_allowlist or "").strip():
        errors.append("已开启 WEBHOOK_REQUIRE_ALLOWLIST 但未配置 WEBHOOK_IP_ALLOWLIST")

    if is_prod and not (settings.webhook_ip_allowlist or "").strip():
        warnings.append("生产建议配置 WEBHOOK_IP_ALLOWLIST")

    process_role = (getattr(settings, "process_role", None) or "").strip().lower()
    if is_prod and process_role not in ("leader", "worker"):
        warnings.append("生产建议显式设置 PROCESS_ROLE=leader|worker（仅一个实例为 leader）")

    # 生产至少配置一种支付通道（微信 V3 或支付宝）；否则小程序/扫码收款不可用
    if is_prod:
        wechat_ok = bool(
            settings.wechat_mch_id
            and settings.wechat_api_v3_key
            and settings.wechat_cert_serial_no
            and settings.wechat_app_id
        )
        alipay_ok = bool(settings.alipay_app_id and settings.alipay_private_key and settings.alipay_public_key)
        if not wechat_ok and not alipay_ok:
            errors.append(
                "生产须至少配置微信（MCH_ID/API_V3_KEY/CERT_SERIAL/APP_ID）或支付宝（APP_ID/私钥/公钥）之一"
            )

    # 打印：开启则须配齐凭证，禁止「开了开关却静默跳过」
    if is_prod:
        if settings.xpyun_enabled:
            if not (
                (settings.xpyun_user or "").strip()
                and (settings.xpyun_user_key or "").strip()
                and (settings.xpyun_printer_sn or "").strip()
            ):
                errors.append(
                    "XPYUN_ENABLED=true 时须配置 XPYUN_USER / XPYUN_USER_KEY / XPYUN_PRINTER_SN；"
                    "暂无打印机请改 XPYUN_ENABLED=false"
                )
        else:
            warnings.append("XPYUN_ENABLED=false：厨房小票不会自动打印")

    for w in warnings:
        logger.warning("启动校验: %s", w)
    if errors:
        for e in errors:
            logger.error("启动校验失败: %s", e)
        sys.stderr.write("\n".join(errors) + "\n")
        raise SystemExit(1)

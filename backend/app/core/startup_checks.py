"""生产启动校验：危险默认配置必须显式覆盖。"""
from __future__ import annotations

import ipaddress
import logging
import re
from urllib.parse import urlsplit
import sys

from app.core.config import Settings

logger = logging.getLogger(__name__)


def _production_redis_url_errors(raw_url: str) -> list[str]:
    """严格解析 redis:// 与 rediss://，不能用字符串中是否含 @ 判断密码。"""
    value = (raw_url or "").strip()
    errors: list[str] = []
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").strip().lower()
        password = parsed.password
    except Exception:
        return ["生产环境 REDIS_URL 格式无效，须为 redis:// 或 rediss:// URL"]
    if parsed.scheme.lower() not in ("redis", "rediss") or not hostname:
        errors.append("生产环境 REDIS_URL 须使用有效的 redis:// 或 rediss:// 地址")
    if hostname in ("localhost", "127.0.0.1", "::1"):
        errors.append("生产环境 REDIS_URL 不得指向 localhost/回环地址，须为生产 Redis 地址")
    if not password:
        errors.append("生产环境 REDIS_URL 必须配置 Redis 密码（支持 ACL username:password）")
    return errors

_DANGEROUS_JWT = {
    "change-me",
    "secret",
    "jwt-secret",
    "",
    "dev-only-change-me-to-random-32chars-min",
    "replace-with-a-long-random-secret-at-least-32chars",
    "replace-with-strong-random-secret-min-32-chars",
}
_DANGEROUS_FACE = {
    "change-me-face-encryption-key-32bytes!!",
    "change-me",
    "",
    "dev-face-key-32bytes!!!!!!!!!!",
    "replace-with-face-encryption-key-32bytes!!",
}
_ALLOWED_APP_ENV = frozenset({"development", "staging", "production", "dev", "local", "prod"})


def _entropy_score(value: str) -> float:
    """粗略熵估计：字符种类 / 长度。弱密钥（重复/字典）得分低。"""
    if not value:
        return 0.0
    classes = 0
    if re.search(r"[a-z]", value):
        classes += 1
    if re.search(r"[A-Z]", value):
        classes += 1
    if re.search(r"[0-9]", value):
        classes += 1
    if re.search(r"[^a-zA-Z0-9]", value):
        classes += 1
    uniq = len(set(value))
    return (classes * 8.0) + (uniq / max(len(value), 1)) * 32.0


def _invalid_ip_allowlist_entries(raw: str) -> list[str]:
    """返回非法 Webhook IP/CIDR 项；生产配置必须至少包含一项有效地址。"""
    parts = [part.strip() for part in (raw or "").split(",") if part.strip()]
    invalid: list[str] = []
    if not parts:
        return ["<empty>"]
    for part in parts:
        try:
            if "/" in part:
                network = ipaddress.ip_network(part, strict=False)
                if network.prefixlen == 0:
                    invalid.append(part)
            else:
                ipaddress.ip_address(part)
        except ValueError:
            invalid.append(part)
    return invalid


_REQUIRED_RATE_LIMIT_FAIL_CLOSED_PATHS = (
    "/api/auth/login",
    "/api/auth/verify-password",
    "/api/payments/",
    "/api/miniprogram/wechat/login",
    "/api/miniprogram/alipay/login",
    "/api/miniprogram/auth/refresh",
)


def _missing_rate_limit_fail_closed_paths(raw: str) -> list[str]:
    """生产必须保证认证、支付及小程序登录路径在 Redis 故障时 fail-closed。"""
    prefixes = [part.strip().rstrip("/") for part in (raw or "").split(",") if part.strip()]
    missing = []
    for target in _REQUIRED_RATE_LIMIT_FAIL_CLOSED_PATHS:
        normalized = target.rstrip("/")
        if not any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in prefixes):
            missing.append(target)
    return missing


def validate_settings(settings: Settings) -> None:
    errors: list[str] = []
    warnings: list[str] = []
    is_prod = bool(getattr(settings, "is_production", False))
    face_liveness_mode = str(
        getattr(settings, "face_liveness_mode", "challenge") or "challenge"
    ).strip().lower()
    if is_prod and face_liveness_mode == "none":
        errors.append(
            "生产环境禁止 FACE_LIVENESS_MODE=none；至少启用一次性 challenge 门控（仍非服务端视觉活体证明）"
        )

    env = str(getattr(settings, "app_env", "") or "").strip().lower()
    if env and env not in _ALLOWED_APP_ENV:
        errors.append(f"APP_ENV 非法: {getattr(settings, 'app_env', None)!r}，仅允许 development/staging/production")
    # 测试用 SimpleNamespace 可能无 app_env；生产路径以 is_production 为准

    jwt = (getattr(settings, "jwt_secret", None) or "").strip()
    if jwt.lower() in {x.lower() for x in _DANGEROUS_JWT} or len(jwt) < 32:
        msg = "JWT_SECRET 过弱或为空，必须为至少 32 字符的高熵随机串"
        (errors if is_prod else warnings).append(msg)
    elif _entropy_score(jwt) < 20:
        msg = "JWT_SECRET 熵过低（重复字符过多），请更换高熵随机串"
        (errors if is_prod else warnings).append(msg)

    alg = (getattr(settings, "jwt_algorithm", None) or "HS256").strip().upper()
    if alg not in ("HS256",):
        msg = f"JWT_ALGORITHM 仅允许 HS256，当前为 {alg!r}"
        (errors if is_prod else warnings).append(msg)

    face = (getattr(settings, "face_encryption_key", None) or "").strip()
    if face in _DANGEROUS_FACE or len(face) < 32:
        msg = "FACE_ENCRYPTION_KEY 为默认值/过短/空，必须更换"
        (errors if is_prod else warnings).append(msg)
    elif _entropy_score(face) < 20:
        msg = "FACE_ENCRYPTION_KEY 熵过低，请更换高熵随机串"
        (errors if is_prod else warnings).append(msg)

    if is_prod and getattr(settings, "app_debug", False):
        errors.append("生产环境禁止 APP_DEBUG=true")

    if is_prod and getattr(settings, "allow_payment_mock", False):
        errors.append("生产环境禁止 ALLOW_PAYMENT_MOCK=true")

    if is_prod and getattr(settings, "allow_auth_mock", False):
        errors.append("生产环境禁止 ALLOW_AUTH_MOCK=true")

    mini_ver = (getattr(settings, "mini_program_env_version", None) or "").strip().lower()
    if is_prod and mini_ver in ("develop", "trial"):
        errors.append(
            "生产环境禁止 MINI_PROGRAM_ENV_VERSION=develop/trial（桌台码会拉起开发/体验版）"
        )

    if is_prod and getattr(settings, "allow_delivery_mock", False):
        errors.append("生产环境禁止 ALLOW_DELIVERY_MOCK=true")

    if is_prod and getattr(settings, "allow_print_mock", False):
        errors.append("生产环境禁止 ALLOW_PRINT_MOCK=true（会导致厨房以为已出票实际未打印）")

    if is_prod and getattr(settings, "allow_opencv_face_fallback", False):
        errors.append("生产环境禁止 ALLOW_OPENCV_FACE_FALLBACK=true")

    if is_prod and not getattr(settings, "enable_rate_limit", True):
        errors.append("生产环境禁止 ENABLE_RATE_LIMIT=false")
    if is_prod:
        missing_rate_limit_paths = _missing_rate_limit_fail_closed_paths(
            getattr(settings, "rate_limit_fail_closed_paths", "") or ""
        )
        if missing_rate_limit_paths:
            errors.append(
                "生产 RATE_LIMIT_FAIL_CLOSED_PATHS 缺少关键路径；Redis 故障时这些接口会降级放行: "
                + ", ".join(missing_rate_limit_paths)
            )

    secret_enc = (getattr(settings, "secret_encryption_key", None) or "").strip()
    if is_prod and not secret_enc:
        errors.append("生产环境须独立配置 SECRET_ENCRYPTION_KEY（勿仅依赖 FACE_ENCRYPTION_KEY）")
    elif is_prod and secret_enc and len(secret_enc) < 32:
        errors.append("SECRET_ENCRYPTION_KEY 须至少 32 字符")

    if is_prod and bool(getattr(settings, "enable_customer_display", False)) and not (
        getattr(settings, "display_screen_token", None) or ""
    ).strip():
        errors.append("已开启大厅叫号屏时，生产环境须配置 DISPLAY_SCREEN_TOKEN")

    cors = (getattr(settings, "cors_origins", None) or "").lower()
    if is_prod and ("localhost" in cors or "127.0.0.1" in cors):
        errors.append("生产环境 CORS_ORIGINS 不得包含 localhost/127.0.0.1，请改为正式前端域名")
    # 规则：带 credentials 时禁止 *（浏览器也会拒；运维误配会导致整站跨域失败）
    cors_raw = (getattr(settings, "cors_origins", None) or "").strip()
    if is_prod and (cors_raw == "*" or any(p.strip() == "*" for p in cors_raw.split(","))):
        errors.append("生产环境 CORS_ORIGINS 不得为 *（须为具体前端域名，且与 Allow-Credentials 兼容）")

    h5_base = (getattr(settings, "h5_public_base_url", None) or "").strip()
    if is_prod and not h5_base:
        errors.append("生产环境须配置 H5_PUBLIC_BASE_URL（顾客扫码/H5 公网基址）")
    elif is_prod and h5_base and ("localhost" in h5_base.lower() or "127.0.0.1" in h5_base):
        errors.append("生产环境 H5_PUBLIC_BASE_URL 不得为 localhost")

    storage = (getattr(settings, "storage_backend", None) or "local").lower()
    if is_prod and storage == "s3":
        if not (
            (getattr(settings, "s3_endpoint", None) or "").strip()
            and (getattr(settings, "s3_access_key", None) or "").strip()
            and (getattr(settings, "s3_secret_key", None) or "").strip()
            and (getattr(settings, "s3_bucket", None) or "").strip()
        ):
            errors.append("STORAGE_BACKEND=s3 时须完整配置 S3_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET")

    if is_prod and storage == "local":
        warnings.append("生产环境建议 STORAGE_BACKEND=s3，避免本地 uploads 直出")

    webhook_allow = (getattr(settings, "webhook_ip_allowlist", None) or "").strip()
    if is_prod:
        invalid_webhook_entries = _invalid_ip_allowlist_entries(webhook_allow)
        if invalid_webhook_entries:
            errors.append(
                "生产 WEBHOOK_IP_ALLOWLIST 必须是有效 IP/CIDR 列表；非法项: "
                + ", ".join(invalid_webhook_entries)
            )

    # P1-41: 生产环境必需配置项不得使用开发默认值
    db_url = (getattr(settings, "database_url", None) or "").strip().lower()
    if is_prod and ("sqlite" in db_url or "localhost" in db_url or "127.0.0.1" in db_url):
        errors.append("生产环境 DATABASE_URL 不得使用 sqlite/localhost，须为生产 MySQL 地址")

    redis_url = (getattr(settings, "redis_url", None) or "").strip()
    if is_prod:
        errors.extend(_production_redis_url_errors(redis_url))
    if is_prod:
        # maxmemory 与持久化为 Redis 服务端配置，代码层无法验证，但须提醒运维检查
        warnings.append(
            "生产 Redis 须配置 maxmemory、容量告警和 AOF/RDB；本项目同一 Redis 同时承载缓存、"
            "限流、一次性凭证与分布式锁，禁止不加区分地使用 allkeys-lru（可能淘汰锁/安全状态键）。"
            "优先拆分缓存 Redis 与协调/安全 Redis；未拆分前须评估 noeviction、预留内存并监控写入拒绝，"
            "参考 redis.conf.example"
        )

    process_role = (getattr(settings, "process_role", None) or "").strip().lower()
    if is_prod and process_role not in ("leader", "worker", "auto"):
        errors.append("生产须显式设置 PROCESS_ROLE=leader|worker|auto（集群仅一个进程持有 Redis Leader 锁）")
    if is_prod and process_role == "leader":
        warnings.append("当前实例为 PROCESS_ROLE=leader；若 gunicorn -w>1，依赖 Redis 锁仅一个 worker 跑调度")
    if is_prod and process_role == "auto":
        warnings.append("PROCESS_ROLE=auto：各进程争抢 Redis Leader 锁，适合单机多 worker")

    # 生产至少配置一种支付通道（微信 V3 或支付宝）；否则小程序/扫码收款不可用
    if is_prod:
        # 占位值检测：REPLACE/your-domain 等示例值一旦上线会静默失效
        _placeholder_re = re.compile(r"replace[_-]|your[-_]domain|your[-_]password|example\.com|<.*>", re.IGNORECASE)

        def _is_placeholder(value: object) -> bool:
            v = str(value or "").strip()
            return bool(v) and bool(_placeholder_re.search(v))

        wechat_ok = bool(
            getattr(settings, "wechat_mch_id", None)
            and getattr(settings, "wechat_api_v3_key", None)
            and getattr(settings, "wechat_cert_serial_no", None)
            and getattr(settings, "wechat_app_id", None)
        )
        if wechat_ok:
            for label in ("wechat_app_secret", "wechat_api_v3_key"):
                if _is_placeholder(getattr(settings, label, None)):
                    errors.append(f"生产配置 {label.upper()} 仍为示例占位值，必须替换为真实密钥")
        alipay_ok = bool(
            getattr(settings, "alipay_app_id", None)
            and getattr(settings, "alipay_private_key", None)
            and getattr(settings, "alipay_public_key", None)
        )
        for label in ("wechat_notify_url", "alipay_notify_url", "meituan_notify_url", "eleme_notify_url"):
            if _is_placeholder(getattr(settings, label, None)):
                errors.append(f"生产配置 {label.upper()} 仍为占位域名（your-domain），回调将无法送达")
        if not wechat_ok and not alipay_ok:
            errors.append(
                "生产须至少配置微信（MCH_ID/API_V3_KEY/CERT_SERIAL/APP_ID）或支付宝（APP_ID/私钥/公钥）之一"
            )
        if alipay_ok and not (
            (getattr(settings, "alipay_seller_id", None) or "").strip()
            or (getattr(settings, "alipay_seller_email", None) or "").strip()
        ):
            errors.append("生产启用支付宝时须配置 ALIPAY_SELLER_ID 或 ALIPAY_SELLER_EMAIL")
        if wechat_ok:
            plat_key = (getattr(settings, "wechat_platform_public_key", None) or "").strip()
            plat_cert = (getattr(settings, "wechat_platform_cert_path", None) or "").strip()
            cert_ok = False
            if plat_key:
                cert_ok = True
                if "BEGIN CERTIFICATE" not in plat_key and not (
                    getattr(settings, "wechat_platform_serial_no", None) or ""
                ).strip():
                    errors.append(
                        "使用独立微信平台公钥 PEM 时必须配置 WECHAT_PLATFORM_SERIAL_NO"
                    )
            elif plat_cert:
                from pathlib import Path

                p = Path(plat_cert)
                if not p.is_absolute():
                    # 相对路径相对项目根 / backend 上级
                    root = Path(__file__).resolve().parents[2]
                    p = (root / plat_cert).resolve()
                cert_ok = p.is_file()
            if not cert_ok:
                errors.append(
                    "已配置微信支付时须提供 WECHAT_PLATFORM_PUBLIC_KEY 或可读的 WECHAT_PLATFORM_CERT_PATH（平台证书/公钥）"
                )

    # 打印：开启则须按模式配齐，禁止「开了开关却静默跳过」
    if is_prod:
        if getattr(settings, "xpyun_enabled", False):
            mode = (getattr(settings, "printer_mode", "local") or "local").strip().lower()
            if mode == "xpyun":
                if not (
                    (getattr(settings, "xpyun_user", None) or "").strip()
                    and (getattr(settings, "xpyun_user_key", None) or "").strip()
                    and (getattr(settings, "xpyun_printer_sn", None) or "").strip()
                ):
                    errors.append(
                        "PRINTER_MODE=xpyun 且 XPYUN_ENABLED=true 时须配置 "
                        "XPYUN_USER / XPYUN_USER_KEY / XPYUN_PRINTER_SN；"
                        "暂无云打印机请改 PRINTER_MODE=local 或 XPYUN_ENABLED=false"
                    )
            elif mode == "local":
                if not (getattr(settings, "printer_host", "") or "").strip():
                    errors.append(
                        "PRINTER_MODE=local 且 XPYUN_ENABLED=true 时须配置 PRINTER_HOST（打印机局域网 IP）；"
                        "暂无打印机请改 XPYUN_ENABLED=false"
                    )
            else:
                errors.append(
                    f"PRINTER_MODE 无效: {getattr(settings, 'printer_mode', None)!r}，仅支持 local 或 xpyun"
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

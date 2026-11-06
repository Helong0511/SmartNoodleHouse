"""生产启动校验与打印失败可见性。"""
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppException
from app.core.startup_checks import validate_settings


def _base_prod(**kwargs):
    data = dict(
        is_production=True,
        app_env="production",
        jwt_secret="K9mP2xQ7vL4nR8wT1yU5zA3bC6dE0fG2hJ4k",
        face_encryption_key="F7nB3qW9sE2mY6tR1uI5oP8aL4xC0vN2zM",
        app_debug=False,
        allow_payment_mock=False,
        allow_auth_mock=False,
        allow_delivery_mock=False,
        allow_print_mock=False,
        storage_backend="s3",
        s3_endpoint="https://s3.example.com",
        s3_access_key="AKIA_TEST",
        s3_secret_key="secret_test_key_not_real",
        s3_bucket="snh-prod",
        webhook_require_allowlist=False,
        webhook_ip_allowlist="203.0.113.0/24",
        display_screen_token="display-token-for-tests-only",
        process_role="leader",
        cors_origins="https://order.example.com",
        h5_public_base_url="https://order.example.com",
        secret_encryption_key="S7nB3qW9sE2mY6tR1uI5oP8aL4xC0vN2zM",
        enable_rate_limit=True,
        allow_opencv_face_fallback=False,
        wechat_mch_id="1900000000",
        wechat_api_v3_key="k" * 32,
        wechat_cert_serial_no="ABC123",
        wechat_app_id="wxapp",
        wechat_platform_public_key="-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0Z3VS5JJcds3xfn/ygWy\n-----END PUBLIC KEY-----",
        wechat_platform_serial_no="TEST-PLATFORM-SERIAL",
        wechat_platform_cert_path="",
        redis_url="rediss://:test-password@redis.example.com:6379/0",
        rate_limit_fail_closed_paths="/api/auth/login,/api/auth/verify-password,/api/payments/,/api/miniprogram/wechat/login,/api/miniprogram/alipay/login,/api/miniprogram/auth/refresh,/api/delivery/meituan/notify,/api/delivery/eleme/notify",
        alipay_app_id="",
        alipay_private_key="",
        alipay_public_key="",
        xpyun_enabled=False,
        printer_mode="local",
        printer_host="",
        xpyun_user="",
        xpyun_user_key="",
        xpyun_printer_sn="",
    )
    data.update(kwargs)
    return SimpleNamespace(**data)


def test_prod_rejects_localhost_cors():
    with pytest.raises(SystemExit):
        validate_settings(_base_prod(cors_origins="http://localhost:5173"))


def test_prod_rejects_empty_h5_base():
    with pytest.raises(SystemExit):
        validate_settings(_base_prod(h5_public_base_url=""))


def test_prod_rejects_wechat_without_platform_cert():
    with pytest.raises(SystemExit):
        validate_settings(_base_prod(wechat_platform_public_key="", wechat_platform_cert_path="missing.pem"))


def test_prod_rejects_print_mock():
    with pytest.raises(SystemExit):
        validate_settings(_base_prod(allow_print_mock=True))


def test_prod_rejects_missing_secret_encryption_key():
    with pytest.raises(SystemExit):
        validate_settings(_base_prod(secret_encryption_key=""))


def test_prod_rejects_rate_limit_disabled():
    with pytest.raises(SystemExit):
        validate_settings(_base_prod(enable_rate_limit=False))


def test_prod_rejects_opencv_face_fallback():
    with pytest.raises(SystemExit):
        validate_settings(_base_prod(allow_opencv_face_fallback=True))


def test_prod_rejects_xpyun_enabled_without_creds():
    with pytest.raises(SystemExit):
        validate_settings(
            _base_prod(
                xpyun_enabled=True,
                printer_mode="xpyun",
                xpyun_user="",
                xpyun_user_key="",
                xpyun_printer_sn="",
            )
        )


def test_prod_rejects_local_enabled_without_host():
    with pytest.raises(SystemExit):
        validate_settings(
            _base_prod(
                xpyun_enabled=True,
                printer_mode="local",
                printer_host="",
            )
        )


def test_prod_ok_when_local_configured():
    validate_settings(
        _base_prod(
            xpyun_enabled=True,
            printer_mode="local",
            printer_host="192.168.1.200",
        )
    )


def test_prod_ok_when_xpyun_fully_configured():
        validate_settings(
        _base_prod(
            xpyun_enabled=True,
            printer_mode="xpyun",
            xpyun_user="u@test.com",
            xpyun_user_key="key",
            xpyun_printer_sn="XPY123",
        )
    )


def test_prod_rejects_no_payment():
    with pytest.raises(SystemExit):
        validate_settings(
            _base_prod(
                wechat_mch_id="",
                wechat_api_v3_key="",
                wechat_cert_serial_no="",
                wechat_app_id="",
            )
        )


@pytest.mark.asyncio
async def test_print_order_raises_when_enabled_but_incomplete(db_session, monkeypatch):
    from decimal import Decimal

    from app.core.biz_no import BIZ_OT, generate_no
    from app.core.config import get_settings
    from app.models import Order, Store
    from app.services.print_service import PrintService

    get_settings.cache_clear()
    monkeypatch.setenv("XPYUN_ENABLED", "true")
    monkeypatch.setenv("PRINTER_MODE", "local")
    monkeypatch.setenv("ALLOW_PRINT_MOCK", "false")
    monkeypatch.setenv("PRINTER_HOST", "")
    get_settings.cache_clear()

    store = Store(id=902, store_no="SH02", name="面馆", status=1)
    db_session.add(store)
    await db_session.flush()
    order = Order(
        id=9002,
        order_no=generate_no(BIZ_OT, store.store_no),
        store_id=store.id,
        status="PAID",
        payment_status="PAID",
        subtotal=Decimal("10.00"),
        total_amount=Decimal("10.00"),
        channel="WECHAT",
    )
    db_session.add(order)
    await db_session.flush()

    with pytest.raises(AppException) as ei:
        await PrintService.print_order(db_session, order.id)
    assert ei.value.code == 50040
    get_settings.cache_clear()

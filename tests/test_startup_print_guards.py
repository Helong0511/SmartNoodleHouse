"""生产启动校验与打印失败可见性。"""
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppException
from app.core.startup_checks import validate_settings


def _base_prod(**kwargs):
    data = dict(
        is_production=True,
        jwt_secret="a" * 40,
        face_encryption_key="b" * 40,
        app_debug=False,
        allow_payment_mock=False,
        allow_auth_mock=False,
        allow_delivery_mock=False,
        allow_print_mock=False,
        storage_backend="s3",
        webhook_require_allowlist=False,
        webhook_ip_allowlist="",
        process_role="leader",
        wechat_mch_id="1900000000",
        wechat_api_v3_key="k" * 32,
        wechat_cert_serial_no="ABC123",
        wechat_app_id="wxapp",
        alipay_app_id="",
        alipay_private_key="",
        alipay_public_key="",
        xpyun_enabled=False,
        xpyun_user="",
        xpyun_user_key="",
        xpyun_printer_sn="",
    )
    data.update(kwargs)
    return SimpleNamespace(**data)


def test_prod_rejects_print_mock():
    with pytest.raises(SystemExit):
        validate_settings(_base_prod(allow_print_mock=True))


def test_prod_rejects_xpyun_enabled_without_creds():
    with pytest.raises(SystemExit):
        validate_settings(_base_prod(xpyun_enabled=True, xpyun_user="", xpyun_user_key="", xpyun_printer_sn=""))


def test_prod_ok_when_xpyun_fully_configured():
    validate_settings(
        _base_prod(
            xpyun_enabled=True,
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
    monkeypatch.setenv("ALLOW_PRINT_MOCK", "false")
    monkeypatch.setenv("XPYUN_USER", "")
    monkeypatch.setenv("XPYUN_USER_KEY", "")
    monkeypatch.setenv("XPYUN_PRINTER_SN", "")
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

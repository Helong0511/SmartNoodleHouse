"""P0 安全与状态机单测（不依赖真实 DB / 平台）"""
import pytest

from app.core.exceptions import AppException
from app.services.order_state_machine import (
    assert_payment_transition,
    assert_staff_transition,
)


def test_staff_cannot_set_paid():
    with pytest.raises(AppException) as ei:
        assert_staff_transition("PENDING_PAYMENT", "PAID")
    assert ei.value.code == 40020


def test_staff_paid_to_confirmed():
    assert_staff_transition("PAID", "CONFIRMED")


def test_staff_illegal_skip():
    with pytest.raises(AppException):
        assert_staff_transition("PENDING_PAYMENT", "COMPLETED")


def test_payment_only_from_pending():
    assert_payment_transition("PENDING_PAYMENT", "PAID")
    with pytest.raises(AppException):
        assert_payment_transition("CONFIRMED", "PAID")


def test_webhook_receipt_key_stable():
    from app.services.webhook_replay import make_receipt_key

    a = make_receipt_key("WECHAT", "n1", "pay1", "tx1")
    b = make_receipt_key("WECHAT", "n1", "pay1", "tx1")
    c = make_receipt_key("WECHAT", "n2", "pay1", "tx1")
    assert a == b and a != c


def test_webhook_backoff_exponential():
    from app.services.webhook_inbox import _backoff_seconds

    assert _backoff_seconds(0) == 1
    assert _backoff_seconds(2) == 4
    assert _backoff_seconds(12) == 3600  # 2^12 封顶


@pytest.mark.asyncio
async def test_finalize_refund_keeps_unknown_not_success():
    from unittest.mock import AsyncMock, MagicMock

    from app.services.payment_service import PaymentService

    row = MagicMock()
    row.status = "PROCESSING"
    row.fail_reason = None
    row.callback_raw = None
    row.provider_refund_no = None
    db = AsyncMock()
    db.flush = AsyncMock()
    await PaymentService._finalize_refund(db, row, "UNKNOWN", source="TEST")
    assert row.status == "UNKNOWN"


@pytest.mark.asyncio
async def test_finalize_refund_processing_not_success():
    from unittest.mock import AsyncMock, MagicMock

    from app.services.payment_service import PaymentService

    row = MagicMock()
    row.status = "PENDING"
    row.fail_reason = None
    row.callback_raw = None
    row.provider_refund_no = None
    db = AsyncMock()
    db.flush = AsyncMock()
    await PaymentService._finalize_refund(db, row, "PROCESSING", source="TEST")
    assert row.status == "PROCESSING"


def test_eleme_shop_id_extract_no_store_fallback():
    from app.services.delivery_context import extract_platform_shop_id

    shop = extract_platform_shop_id(
        "ELEME",
        {"shopId": "E123"},
        {"platform_shop_id": "", "message": {"shopId": "E999"}},
    )
    assert shop == "E123"


def test_meituan_sign_roundtrip():
    from app.services.delivery_sign import meituan_sign, meituan_verify_callback

    url = "https://example.com/api/v1/delivery/meituan/notify"
    params = {"order_id": "1", "app_id": "app", "timestamp": "1"}
    secret = "secret"
    params["sig"] = meituan_sign(url, params, secret)
    assert meituan_verify_callback(url, params, secret, double_decode=False)


def test_alipay_sign_content_sorted():
    from app.services.payment_verify import AlipayVerifier

    content = AlipayVerifier._build_sign_content({"b": "2", "a": "1", "sign": "x"})
    assert content == "a=1&b=2"

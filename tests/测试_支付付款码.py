"""付款码渠道识别"""
import pytest

from app.core.exceptions import AppException
from app.services.payment_barcode import detect_barcode_provider, resolve_barcode_provider


def test_detect_wechat_barcode():
    assert detect_barcode_provider("101234567890123456") == "WECHAT"
    assert detect_barcode_provider("151234567890123456") == "WECHAT"


def test_detect_alipay_barcode():
    assert detect_barcode_provider("2812345678901234") == "ALIPAY"
    assert detect_barcode_provider("30123456789012345678") == "ALIPAY"


def test_detect_invalid_barcode():
    with pytest.raises(AppException) as exc:
        detect_barcode_provider("991234567890123456")
    assert exc.value.code == 40041


def test_resolve_prefers_detect_over_wrong_explicit():
    assert resolve_barcode_provider("101234567890123456", "ALIPAY") == "WECHAT"


def test_resolve_prefers_explicit_provider():
    assert resolve_barcode_provider("991234567890123456", "WECHAT") == "WECHAT"

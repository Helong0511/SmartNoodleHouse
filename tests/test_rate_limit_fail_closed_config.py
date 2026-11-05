from app.core.config import Settings
from app.core.startup_checks import (
    _REQUIRED_RATE_LIMIT_FAIL_CLOSED_PATHS,
    _missing_rate_limit_fail_closed_paths,
)


def test_delivery_callbacks_are_required_fail_closed_paths():
    required = set(_REQUIRED_RATE_LIMIT_FAIL_CLOSED_PATHS)
    assert "/api/delivery/meituan/notify" in required
    assert "/api/delivery/eleme/notify" in required


def test_default_rate_limit_configuration_covers_delivery_callbacks():
    settings = Settings()
    missing = _missing_rate_limit_fail_closed_paths(
        settings.rate_limit_fail_closed_paths
    )
    assert missing == []


def test_delivery_callback_prefixes_cannot_be_omitted_in_production_config():
    configured = (
        "/api/auth/login,/api/auth/verify-password,/api/payments/,"
        "/api/miniprogram/wechat/login,/api/miniprogram/alipay/login,"
        "/api/miniprogram/auth/refresh"
    )
    missing = _missing_rate_limit_fail_closed_paths(configured)
    assert "/api/delivery/meituan/notify" in missing
    assert "/api/delivery/eleme/notify" in missing

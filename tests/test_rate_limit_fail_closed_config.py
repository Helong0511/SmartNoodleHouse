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


def test_redis_outage_logs_are_throttled_to_prevent_log_storms(monkeypatch):
    from app.core import rate_limit

    clock = {"now": 100.0}
    calls = []
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(rate_limit.logger, "warning", lambda *args: calls.append(args))
    rate_limit._last_redis_error_log_at = 0.0
    rate_limit._suppressed_redis_error_logs = 0

    error = RuntimeError("redis unavailable")
    rate_limit._log_redis_failure("/api/menu", error, fail_closed=False)
    clock["now"] += 1
    rate_limit._log_redis_failure("/api/menu", error, fail_closed=False)
    assert len(calls) == 1

    clock["now"] += rate_limit._REDIS_ERROR_LOG_INTERVAL_SEC
    rate_limit._log_redis_failure("/api/menu", error, fail_closed=False)
    assert len(calls) == 2
    assert "suppressed=%s" in calls[1][0]
    assert calls[1][2] == 1

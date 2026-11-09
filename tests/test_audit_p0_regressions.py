"""代码审查报告 P0 回归：配置 fail-closed 与日志凭据脱敏。"""
import logging

import pytest

from app.core.config import Settings
from app.core.logging_config import SensitiveQueryRedactionFilter


def test_only_explicit_development_environment_is_non_production():
    assert Settings(_env_file=None, app_env="development").is_development
    assert not Settings(_env_file=None, app_env="development").is_production
    for env in ("", "typo-production", "staging", "producton"):
        settings = Settings(_env_file=None, app_env=env)
        assert settings.is_production, env
        assert not settings.is_development, env


def test_default_app_environment_is_fail_closed(monkeypatch):
    # 同时清除测试 conftest 注入的开发环境，验证真正的无配置默认值。
    monkeypatch.delenv("APP_ENV", raising=False)
    settings = Settings(_env_file=None)
    assert settings.is_production
    assert not settings.is_development


def test_sensitive_query_parameters_are_redacted_from_log_message():
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='HTTP Request: GET https://api.weixin.qq.com/cgi-bin/token?appid=wx123&secret=very-secret-value "HTTP/1.1 200 OK"',
        args=(),
        exc_info=None,
    )
    assert SensitiveQueryRedactionFilter().filter(record)
    rendered = record.getMessage()
    assert "very-secret-value" not in rendered
    assert "secret=[REDACTED]" in rendered

def test_schema_initializer_does_not_drop_database():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    schema = (root / "sql" / "struct.sql").read_text(encoding="utf-8")
    assert "DROP DATABASE" not in schema.upper()


def test_destructive_system_seed_requires_explicit_confirmation_before_truncate():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    seed = (root / "sql" / "data_sys.sql").read_text(encoding="utf-8")
    guard = seed.index("@SMART_NOODLE_FRESH_INSTALL_CONFIRM")
    truncate = seed.upper().index("TRUNCATE TABLE")
    assert guard < truncate
    assert "SIGNAL SQLSTATE '45000'" in seed
    assert "DATABASE() <> 'smart_noodle_house'" in seed


def test_immediate_payment_amount_is_required_and_must_match():
    from decimal import Decimal

    import pytest

    from app.core.exceptions import AppException
    from app.services.payment_service import _require_provider_amount

    with pytest.raises(AppException):
        _require_provider_amount({}, Decimal("12.50"))
    with pytest.raises(AppException):
        _require_provider_amount({"provider_amount": "12.49"}, Decimal("12.50"))
    _require_provider_amount({"provider_amount": "12.50"}, Decimal("12.50"))

@pytest.mark.asyncio
async def test_login_guard_fails_closed_when_redis_is_unavailable(monkeypatch):
    import app.core.redis_client as redis_client
    from app.api import auth
    from app.core.exceptions import AppException

    async def unavailable():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(redis_client, "get_redis", unavailable)
    for operation in (
        auth._login_is_locked,
        auth._record_login_failure,
        auth._clear_login_failures,
    ):
        with pytest.raises(AppException) as exc:
            await operation("audit-test-user", "127.0.0.1")
        assert exc.value.status_code == 503

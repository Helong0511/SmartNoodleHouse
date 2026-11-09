"""代码审查报告 P0 回归：配置 fail-closed 与日志凭据脱敏。"""
import logging

from app.core.config import Settings
from app.core.logging_config import SensitiveQueryRedactionFilter


def test_only_explicit_development_environment_is_non_production():
    assert Settings(_env_file=None, app_env="development").is_development
    assert not Settings(_env_file=None, app_env="development").is_production
    for env in ("", "typo-production", "staging", "producton"):
        settings = Settings(_env_file=None, app_env=env)
        assert settings.is_production, env
        assert not settings.is_development, env


def test_default_app_environment_is_fail_closed():
    # 显式 _env_file=None 避免读取开发者本机 .env。
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

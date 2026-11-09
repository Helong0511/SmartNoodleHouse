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
    assert "USE `smart_noodle_house`;" in seed
    assert seed.index("USE `smart_noodle_house`;") < seed.index("CREATE PROCEDURE")
    assert "SIGNAL SQLSTATE '45000'" in seed
    assert "COALESCE(DATABASE(), '') <> 'smart_noodle_house'" in seed
    assert "DATABASE() <> 'smart_noodle_house'" not in seed


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

def test_wechat_refund_receipt_identity_does_not_depend_on_success_time():
    from app.services.payment_service import _wechat_callback_receipt_identity

    first = {
        "id": "wx-notify-001",
        "event_type": "REFUND.SUCCESS",
        "success_time": "2026-11-08T10:00:00+08:00",
    }
    second = {
        **first,
        "success_time": "2026-11-08T10:00:05+08:00",
    }
    parsed = {
        "is_refund": True,
        "refund_no": "RF-001",
        "refund_id": "provider-refund-001",
    }
    assert _wechat_callback_receipt_identity(first, parsed) == _wechat_callback_receipt_identity(second, parsed)


def test_refund_success_requires_all_provider_identity_fields():
    from decimal import Decimal
    from types import SimpleNamespace

    import pytest

    from app.core.exceptions import AppException
    from app.services.payment_service import PaymentService

    payment = SimpleNamespace(
        id=1, order_id=9, payment_no="PAY-001", provider_trade_no="WX-TRADE-001"
    )
    refund = SimpleNamespace(
        id=2, payment_id=1, order_id=9, refund_no="REF-001", amount=Decimal("3.00")
    )
    with pytest.raises(AppException):
        PaymentService._validate_refund_query_result(
            provider="ALIPAY",
            payment=payment,
            refund=refund,
            result={"status": "SUCCESS", "amount": "3.00"},
        )
    assert PaymentService._validate_refund_query_result(
        provider="ALIPAY",
        payment=payment,
        refund=refund,
        result={
            "status": "SUCCESS",
            "refund_no": "REF-001",
            "payment_no": "PAY-001",
            "provider_trade_no": "WX-TRADE-001",
            "amount": "3.00",
        },
    ) == "SUCCESS"


def test_intrusion_probe_ignores_user_agent_and_rejects_prefix_exemption_bypass():
    from starlette.requests import Request

    from app.core.intrusion_detection import _is_exempt, detect_intrusion

    assert not _is_exempt("/api/payments/wechat/notify-evil")
    assert _is_exempt("/api/payments/wechat/notify/extra")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/search",
            "raw_path": b"/api/search",
            "query_string": b"q=hello",
            "headers": [(b"user-agent", b"scanner union select")],
            "client": ("127.0.0.1", 12345),
            "server": ("localhost", 80),
            "scheme": "http",
        }
    )
    assert detect_intrusion(request) == (False, "")


def test_social_benefit_requires_explicit_opt_in_not_legacy_rule_id():
    from app.services.order_create_service import OrderCreateService

    assert not OrderCreateService._should_apply_social_benefit(123, False)
    assert OrderCreateService._should_apply_social_benefit(123, True)
    assert not OrderCreateService._should_apply_social_benefit(None, True)


@pytest.mark.asyncio
async def test_staff_payment_access_rejects_orphan_and_cross_store_recharge(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.api import deps, payments
    from app.core.exceptions import AppException

    monkeypatch.setattr(
        deps, "get_user_permissions", AsyncMock(return_value={"payment:read"})
    )
    user = SimpleNamespace(
        id=7, role_id=3, role=SimpleNamespace(code="STORE_ADMIN"), store_id=1
    )

    orphan_db = SimpleNamespace(get=AsyncMock(return_value=None))
    with pytest.raises(AppException):
        await payments._authorize_staff_payment_access(
            orphan_db, user, SimpleNamespace(recharge_order_id=None), None
        )

    foreign_db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(store_id=2)))
    with pytest.raises(AppException):
        await payments._authorize_staff_payment_access(
            foreign_db, user, SimpleNamespace(recharge_order_id=99), None
        )

    own_db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(store_id=1)))
    await payments._authorize_staff_payment_access(
        own_db, user, SimpleNamespace(recharge_order_id=99), None
    )

def test_explicit_development_with_remote_dependencies_is_fail_closed():
    settings = Settings(
        _env_file=None,
        app_env="development",
        database_url="mysql+aiomysql://app:secret@db.example.net:3306/smart_noodle_house",
        redis_url="redis://127.0.0.1:6379/0",
        wechat_mch_id="",
        wechat_api_v3_key="",
        wechat_cert_serial_no="",
    )
    assert settings.is_production


def test_local_development_without_production_markers_stays_development():
    settings = Settings(
        _env_file=None,
        app_env="development",
        database_url="mysql+aiomysql://app:secret@127.0.0.1:3306/smart_noodle_house",
        redis_url="redis://localhost:6379/0",
        wechat_mch_id="",
        wechat_api_v3_key="",
        wechat_cert_serial_no="",
    )
    assert settings.is_development


def test_explicit_development_with_real_payment_merchant_credentials_is_fail_closed():
    settings = Settings(
        _env_file=None,
        app_env="development",
        database_url="mysql+aiomysql://app:secret@127.0.0.1:3306/smart_noodle_house",
        redis_url="redis://127.0.0.1:6379/0",
        wechat_mch_id="1900000001",
        wechat_api_v3_key="A" * 32,
        wechat_cert_serial_no="CERT-SERIAL",
    )
    assert settings.is_production


def test_client_logs_redact_phone_and_identifiers_and_allowlist_extra():
    from app.api.client_logs import ClientLogEntry

    entry = ClientLogEntry(
        message="customer_id=123 phone=13800138000 /orders/ORDER-123",
        extra={
            "status": 503,
            "code": "AUTH_STALE",
            "phone": "13800138000",
            "order_id": "ORDER-123",
            "raw_response": {"mobile": "13800138000"},
        },
    )
    assert "13800138000" not in entry.message
    assert "ORDER-123" not in entry.message
    assert "123" not in entry.message
    assert entry.extra == {"status": 503, "code": "AUTH_STALE"}


def test_wechat_order_result_imports_subscription_helper_before_use():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    page = (root / "miniprogram-wechat/packageOrder/result/result.js").read_text(encoding="utf-8")
    helper = root / "miniprogram-wechat/utils/subscribe.js"
    assert helper.is_file()
    assert "const subscribe = require('../../utils/subscribe')" in page
    assert "subscribe.requestOrderReadySubscribe()" in page

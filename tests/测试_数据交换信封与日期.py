"""数据交换：422 统一信封、日期过滤、client-logs 上限对齐。"""
from datetime import datetime

import pytest
from pydantic import ValidationError


def test_apply_date_range_date_only_includes_full_day():
    from sqlalchemy import Column, DateTime, Integer, MetaData, Table, create_engine, select
    from sqlalchemy.orm import Session

    from app.core.response import apply_date_range

    meta = MetaData()
    t = Table(
        "tmp_date_range",
        meta,
        Column("id", Integer, primary_key=True),
        Column("created_at", DateTime),
    )
    eng = create_engine("sqlite:///:memory:")
    meta.create_all(eng)
    with Session(eng) as s:
        s.execute(
            t.insert(),
            [
                {"id": 1, "created_at": datetime(2026, 10, 10, 0, 0, 0)},
                {"id": 2, "created_at": datetime(2026, 10, 10, 23, 59, 59)},
                {"id": 3, "created_at": datetime(2026, 10, 11, 0, 0, 0)},
            ],
        )
        s.commit()
        q = select(t.c.id)
        q = apply_date_range(q, t.c.created_at, "2026-10-10", "2026-10-10")
        ids = [r[0] for r in s.execute(q).all()]
        assert ids == [1, 2]


def test_apply_date_range_rejects_invalid():
    from sqlalchemy import Column, DateTime, Integer, MetaData, Table, select

    from app.core.exceptions import AppException
    from app.core.response import apply_date_range

    meta = MetaData()
    t = Table("tmp2", meta, Column("id", Integer), Column("created_at", DateTime))
    q = select(t.c.id)
    with pytest.raises(AppException) as ei:
        apply_date_range(q, t.c.created_at, "not-a-date", None)
    assert ei.value.code == 40001


def test_validation_error_formatter():
    from app.core.exceptions import _format_validation_errors
    from app.schemas.order import OrderCreate

    try:
        OrderCreate(items=[])
    except ValidationError as e:
        msg = _format_validation_errors(list(e.errors()))
        assert "items" in msg or "至少" in msg or "min" in msg.lower() or msg


def test_payment_create_rejects_bad_provider():
    from app.schemas.order import PaymentCreate

    with pytest.raises(ValidationError):
        PaymentCreate(provider="CASH", channel_type="native")


def test_client_log_batch_max_entries():
    from app.api.client_logs import ClientLogBatch, _MAX_ENTRIES

    assert _MAX_ENTRIES == 30
    entries = [{"level": "INFO", "message": "x"} for _ in range(31)]
    with pytest.raises(ValidationError):
        ClientLogBatch(source="frontend", entries=entries)


def test_require_money_rejects_none():
    from app.core.exceptions import AppException
    from app.core.response import require_money

    with pytest.raises(AppException):
        require_money(None, field="amount")
    assert str(require_money("12.3")) == "12.30"


def test_serialize_dt_adds_timezone():
    from datetime import date, datetime
    from decimal import Decimal
    import json

    from app.core.response import dumps_json, serialize_dt

    naive = datetime(2026, 10, 10, 12, 0, 0)
    out = serialize_dt(naive)
    assert out is not None
    assert "+" in out or out.endswith("Z")
    assert out.startswith("2026-10-10T12:00:00")
    assert serialize_dt(date(2026, 10, 10)) == "2026-10-10"
    assert serialize_dt(None) is None


def test_dumps_json_decimal_and_rejects_nan():
    from datetime import datetime
    from decimal import Decimal
    import json

    from app.core.response import dumps_json

    naive = datetime(2026, 10, 10, 12, 0, 0)
    with pytest.raises(ValueError):
        dumps_json({"x": float("nan")})
    parsed = json.loads(dumps_json({"amt": Decimal("1.20"), "at": naive}))
    assert parsed["amt"] == "1.20"
    assert "+" in parsed["at"] or parsed["at"].endswith("Z")


def test_api_layer_no_raise_http_exception():
    """API/deps/门店隔离层业务失败走 AppException，禁止再 raise HTTPException。"""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "backend" / "app"
    targets = [
        "api/deps.py",
        "api/payments.py",
        "api/orders.py",
        "api/delivery.py",
        "api/customers.py",
        "api/pos.py",
        "api/settings.py",
        "api/products.py",
        "api/cameras.py",
        "api/member_levels.py",
        "services/order_access.py",
        "services/store_scope.py",
    ]
    for rel in targets:
        src = (root / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                func = node.exc.func
                if isinstance(func, ast.Name) and func.id == "HTTPException":
                    raise AssertionError(f"{rel} 仍 raise HTTPException")
                if isinstance(func, ast.Attribute) and func.attr == "HTTPException":
                    raise AssertionError(f"{rel} 仍 raise HTTPException")


def test_app_exception_maps_http_status_from_biz_code():
    from app.core.exceptions import AppException

    assert AppException(40404, "订单不存在").status_code == 404
    assert AppException(40301, "无权").status_code == 403
    assert AppException(40100, "未登录").status_code == 401
    assert AppException(42901, "限流").status_code == 429
    assert AppException(50301, "未配置").status_code == 503
    assert AppException(40001, "参数错").status_code == 400
    assert AppException(40404, "x", status_code=410).status_code == 410
    assert AppException(41001, "已废弃").status_code == 410


def test_apply_date_range_china_offset_normalized():
    """带 +08:00 的 ISO 边界应落到中国本地 naive，与库内 DATETIME 对齐。"""
    from datetime import datetime
    from sqlalchemy import Column, DateTime, Integer, MetaData, Table, create_engine, select
    from sqlalchemy.orm import Session

    from app.core.response import apply_date_range

    meta = MetaData()
    t = Table(
        "tmp_tz_range",
        meta,
        Column("id", Integer, primary_key=True),
        Column("created_at", DateTime),
    )
    eng = create_engine("sqlite:///:memory:")
    meta.create_all(eng)
    with Session(eng) as s:
        s.execute(
            t.insert(),
            [
                {"id": 1, "created_at": datetime(2026, 10, 10, 8, 0, 0)},
                {"id": 2, "created_at": datetime(2026, 10, 10, 23, 0, 0)},
                {"id": 3, "created_at": datetime(2026, 10, 11, 0, 0, 0)},
            ],
        )
        s.commit()
        q = select(t.c.id)
        q = apply_date_range(
            q,
            t.c.created_at,
            "2026-10-10T00:00:00+08:00",
            "2026-10-10T23:59:59+08:00",
        )
        ids = [r[0] for r in s.execute(q).all()]
        assert ids == [1, 2]


def _http_request(path: str, request_id: str = "rid-envelope"):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"x-request-id", request_id.encode())],
        "client": ("10.0.0.8", 12345),
        "server": ("test", 80),
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_rate_limit_reject_envelope_has_request_id():
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.core.rate_limit import RateLimitMiddleware

    mw = RateLimitMiddleware(app=MagicMock())
    request = _http_request("/api/orders")
    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=999)
    settings = SimpleNamespace(
        is_production=False,
        is_development=False,
        enable_rate_limit=True,
        rate_limit_fail_closed_paths="",
    )
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        with patch("app.core.rate_limit.get_redis", AsyncMock(return_value=redis)):
            with patch("app.core.rate_limit.client_ip", return_value="10.0.0.8"):
                resp = await mw.dispatch(request, AsyncMock())
    body = json.loads(resp.body)
    assert resp.status_code == 429
    assert body["code"] == 429
    assert body["data"] is None
    assert body["request_id"] == "rid-envelope"
    assert resp.headers["x-request-id"] == "rid-envelope"


@pytest.mark.asyncio
async def test_webhook_allowlist_reject_envelope_has_request_id():
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.core.ip_allowlist import WebhookAllowlistMiddleware

    mw = WebhookAllowlistMiddleware(app=MagicMock())
    request = _http_request("/api/payments/wechat/notify")
    settings = SimpleNamespace(is_production=True, webhook_ip_allowlist="203.0.113.0/24")
    with patch("app.services.system_settings_service.SystemSettingsService.touch_cache", AsyncMock()):
        with patch("app.core.ip_allowlist.get_settings", return_value=settings):
            with patch(
                "app.services.system_settings_service.SystemSettingsService.resolved_str_sync",
                return_value="203.0.113.0/24",
            ):
                with patch("app.core.ip_allowlist.client_ip", return_value="198.51.100.9"):
                    resp = await mw.dispatch(request, AsyncMock())
    body = json.loads(resp.body)
    assert resp.status_code == 403
    assert body["code"] == 403
    assert body["data"] is None
    assert body["message"]
    assert body["request_id"] == "rid-envelope"
    assert resp.headers["x-request-id"] == "rid-envelope"

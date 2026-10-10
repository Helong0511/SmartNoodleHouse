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
    from app.api.v1.client_logs import ClientLogBatch, _MAX_ENTRIES

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

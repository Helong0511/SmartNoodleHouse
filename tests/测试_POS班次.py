"""收银班次与日结逻辑测试。"""
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.pos_shift_service import PosShiftService, _day_bounds, _money


def test_money_none():
    assert _money(None) == Decimal("0.00")


def test_day_bounds_default():
    start, end = _day_bounds(None)
    assert start < end
    assert (end - start).days == 1


@pytest.mark.asyncio
async def test_require_open_shift_missing():
    db = AsyncMock()
    PosShiftService.get_open_shift = AsyncMock(return_value=None)
    with pytest.raises(Exception) as ei:
        await PosShiftService.require_open_shift(db, operator_id=9, store_id=1)
    assert "开班" in str(ei.value)


@pytest.mark.asyncio
async def test_require_open_shift_store_mismatch():
    db = AsyncMock()
    shift = MagicMock()
    shift.store_id = 2
    PosShiftService.get_open_shift = AsyncMock(return_value=shift)
    with pytest.raises(Exception) as ei:
        await PosShiftService.require_open_shift(db, operator_id=9, store_id=1)
    assert "门店" in str(ei.value)


@pytest.mark.asyncio
async def test_require_open_shift_ok():
    db = AsyncMock()
    shift = MagicMock()
    shift.store_id = 1
    PosShiftService.get_open_shift = AsyncMock(return_value=shift)
    got = await PosShiftService.require_open_shift(db, operator_id=9, store_id=1)
    assert got is shift


@pytest.mark.asyncio
async def test_list_shift_orders_empty():
    from datetime import timezone

    db = AsyncMock()
    shift = MagicMock()
    shift.id = 1
    shift.operator_id = 2
    shift.opened_at = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    shift.closed_at = None

    count_result = MagicMock()
    count_result.scalar.return_value = 0
    list_result = MagicMock()
    list_result.scalars.return_value.all.return_value = []

    db.execute = AsyncMock(side_effect=[count_result, list_result])
    data = await PosShiftService.list_shift_orders(db, shift)
    assert data["total"] == 0
    assert data["items"] == []

 
@pytest.mark.asyncio
async def test_require_open_shift_for_update_locks_and_checks_store(monkeypatch):
    shift = MagicMock()
    shift.store_id = 4
    monkeypatch.setattr(PosShiftService, "get_open_shift_for_update", AsyncMock(return_value=shift))
    got = await PosShiftService.require_open_shift_for_update(
        AsyncMock(), operator_id=9, store_id=4
    )
    assert got is shift


@pytest.mark.asyncio
async def test_close_shift_refuses_pending_barcode_payment():
    from app.core.exceptions import AppException
    from datetime import timezone

    shift = MagicMock()
    shift.id = 12
    shift.operator_id = 9
    shift.status = "OPEN"
    shift.opened_at = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)

    locked_result = MagicMock()
    locked_result.scalar_one_or_none.return_value = shift
    pending_result = MagicMock()
    pending_result.scalars.return_value.all.return_value = [88]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[locked_result, pending_result])

    with pytest.raises(AppException) as exc:
        await PosShiftService.close_shift(
            db, shift, closing_cash=Decimal("0"), remark=None, closed_by=9
        )

    assert exc.value.status_code == 409
    assert "待确认" in str(exc.value)

 
@pytest.mark.asyncio
async def test_shift_totals_use_current_read_and_include_late_callback_payment():
    from datetime import timezone
    from types import SimpleNamespace

    from sqlalchemy.dialects import mysql

    from app.services.pos_shift_service import _aggregate_payments

    since = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    closed_at = datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)
    late_payment = SimpleNamespace(
        provider="WECHAT",
        amount=Decimal("15.00"),
        paid_at=datetime(2026, 1, 1, 18, 5, tzinfo=timezone.utc),
        cashier_shift_id=12,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [late_payment]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    stats = await _aggregate_payments(
        db,
        operator_id=9,
        since=since,
        until=closed_at,
        store_id=4,
        cashier_shift_id=12,
    )

    statement = db.execute.await_args.args[0]
    compiled = str(statement.compile(dialect=mysql.dialect()))
    assert "FOR UPDATE" in compiled
    assert "cashier_shift_id" in compiled
    assert stats["total_wechat"] == Decimal("15.00")
    assert stats["total_amount"] == Decimal("15.00")


@pytest.mark.asyncio
async def test_my_today_summary_scopes_cash_refunds_to_effective_store():
    from types import SimpleNamespace
    from unittest.mock import patch

    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(store_id=7))
    db.scalar = AsyncMock(return_value=Decimal("2.00"))
    stats = {
        "total_cash": Decimal("10.00"),
        "total_wechat": Decimal("0.00"),
        "total_alipay": Decimal("0.00"),
        "total_amount": Decimal("10.00"),
    }
    with patch.object(
        PosShiftService, "get_open_shift", new_callable=AsyncMock, return_value=None
    ), patch(
        "app.services.pos_shift_service._aggregate_payments",
        new_callable=AsyncMock,
        return_value=stats,
    ):
        result = await PosShiftService.my_today_summary(db, operator_id=9)

    query = db.scalar.await_args.args[0]
    assert "cashier_shifts.store_id" in str(query)
    assert result["total_cash_refunds"] == "2.00"


@pytest.mark.asyncio
async def test_my_today_summary_does_not_filter_store_for_unbound_operator():
    from types import SimpleNamespace
    from unittest.mock import patch

    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(store_id=None))
    db.scalar = AsyncMock(return_value=Decimal("2.00"))
    stats = {
        "total_cash": Decimal("10.00"),
        "total_wechat": Decimal("0.00"),
        "total_alipay": Decimal("0.00"),
        "total_amount": Decimal("10.00"),
    }
    with patch.object(
        PosShiftService, "get_open_shift", new_callable=AsyncMock, return_value=None
    ), patch(
        "app.services.pos_shift_service._aggregate_payments",
        new_callable=AsyncMock,
        return_value=stats,
    ):
        await PosShiftService.my_today_summary(db, operator_id=9)

    query = db.scalar.await_args.args[0]
    assert "cashier_shifts.store_id" not in str(query)

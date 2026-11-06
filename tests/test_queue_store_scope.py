"""Regression tests for store-scoped staff queue access."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import AppException


@pytest.mark.asyncio
async def test_admin_queue_list_uses_bound_store_even_if_other_store_requested(monkeypatch):
    import app.api.queue as queue_api

    monkeypatch.setattr(
        queue_api,
        "now_local",
        lambda: datetime(2026, 11, 6, tzinfo=timezone.utc),
    )

    user = SimpleNamespace(
        role=SimpleNamespace(code="STORE_ADMIN"),
        store_id=101,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    await queue_api.admin_today_tickets(
        request=None,
        db=db,
        user=user,
        store_id=202,
    )

    statement = db.execute.await_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "store_id = 101" in sql
    assert "store_id = 202" not in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "ticket_status"),
    [
        ("admin_call_ticket", "WAITING"),
        ("admin_seat_ticket", "CALLED"),
        ("admin_cancel_ticket", "WAITING"),
    ],
)
async def test_staff_queue_mutations_reject_other_store_ticket(
    monkeypatch, handler_name, ticket_status
):
    import app.api.queue as queue_api

    ticket = SimpleNamespace(
        id=501,
        store_id=202,
        status=ticket_status,
        ticket_no="A001",
        number=1,
    )
    monkeypatch.setattr(
        queue_api,
        "_get_queue_ticket",
        AsyncMock(return_value=ticket),
    )
    user = SimpleNamespace(
        role=SimpleNamespace(code="STORE_ADMIN"),
        store_id=101,
    )
    handler = getattr(queue_api, handler_name)

    with pytest.raises(AppException) as exc:
        await handler(ticket_id=501, request=None, db=SimpleNamespace(), user=user)
    assert exc.value.status_code == 404


def test_unbound_staff_cannot_operate_queue_ticket():
    from app.api.queue import _assert_staff_ticket_store

    user = SimpleNamespace(
        role=SimpleNamespace(code="STORE_ADMIN"),
        store_id=None,
    )
    ticket = SimpleNamespace(store_id=202)
    with pytest.raises(AppException) as exc:
        _assert_staff_ticket_store(user, ticket)
    assert exc.value.status_code == 403

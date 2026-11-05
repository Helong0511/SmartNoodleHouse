"""PrintJob 持久化与状态迁移测试"""
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.core.biz_no import BIZ_OT, generate_no
from app.core.exceptions import AppException
from app.models import Order, OrderItem, PrintJob, Store
from app.services.print_service import (
    MAX_PRINT_ATTEMPTS,
    PrintService,
    _escape_print_text,
    _prepare_job_content,
    _strip_print_controls,
)


def test_sanitize_strips_esc_gs_and_controls():
    raw = "备注\x1b@注入\x1dV\x00\x07你好<script>"
    assert "\x1b" not in _strip_print_controls(raw)
    assert "\x1d" not in _strip_print_controls(raw)
    assert "\x00" not in _strip_print_controls(raw)
    escaped = _escape_print_text(raw)
    assert "<" not in escaped or "&lt;" in escaped
    assert "&lt;script&gt;" in escaped
    prepared = _prepare_job_content(f"<C>店名</C><BR>{raw}<CUT>")
    assert "\x1b" not in prepared
    assert "\x1d" not in prepared
    assert "<C>店名</C>" in prepared  # 整票标签保留，仅清控制符


def test_prepare_job_content_truncates():
    from app.services import print_service as ps

    big = "面" * (ps.PRINT_CONTENT_MAX_LEN + 100)
    out = _prepare_job_content(big)
    assert len(out) == ps.PRINT_CONTENT_MAX_LEN


async def _seed_paid_order(db_session):
    store = Store(id=901, store_no="SH01", name="面馆", status=1)
    db_session.add(store)
    await db_session.flush()
    order = Order(
        id=9001,
        order_no=generate_no(BIZ_OT, store.store_no),
        store_id=store.id,
        status="PAID",
        payment_status="PAID",
        subtotal=Decimal("10.00"),
        total_amount=Decimal("10.00"),
        channel="WECHAT",
        remark="含\x1b控制",
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        OrderItem(
            id=9002,
            order_id=order.id,
            product_id=1,
            product_name_snapshot="测试面",
            unit_price_snapshot=Decimal("10.00"),
            quantity=1,
            subtotal=Decimal("10.00"),
        )
    )
    await db_session.flush()
    return store, order


@pytest.mark.asyncio
async def test_print_order_success_marks_print_job(db_session, monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("XPYUN_ENABLED", "true")
    monkeypatch.setenv("PRINTER_MODE", "local")
    monkeypatch.setenv("PRINTER_HOST", "192.168.1.200")
    get_settings.cache_clear()

    _, order = await _seed_paid_order(db_session)

    with patch("app.services.print_service.EscposClient.print_ticket", new_callable=AsyncMock) as mock_print:
        mock_print.return_value = {"code": 0, "msg": "ok", "mode": "local"}
        result = await PrintService.print_order(db_session, order.id)
        assert result["code"] == 0
        assert result.get("print_job_id")

    job = (
        await db_session.execute(select(PrintJob).where(PrintJob.order_id == order.id))
    ).scalar_one()
    assert job.status == "SUCCESS"
    assert job.attempt_count == 1
    assert job.succeeded_at is not None
    assert job.content_hash
    assert job.content
    assert "\x1b" not in (job.content or "")
    assert job.last_error is None
    assert job.locked_at is None
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_print_order_failure_marks_retrying(db_session, monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("XPYUN_ENABLED", "true")
    monkeypatch.setenv("PRINTER_MODE", "local")
    monkeypatch.setenv("PRINTER_HOST", "192.168.1.200")
    get_settings.cache_clear()

    _, order = await _seed_paid_order(db_session)

    with patch("app.services.print_service.EscposClient.print_ticket", new_callable=AsyncMock) as mock_print:
        mock_print.side_effect = RuntimeError("connection timed out")
        with pytest.raises(AppException):
            await PrintService.print_order(db_session, order.id)

    job = (
        await db_session.execute(select(PrintJob).where(PrintJob.order_id == order.id))
    ).scalar_one()
    assert job.status == "RETRYING"
    assert job.attempt_count == 1
    assert job.next_retry_at is not None
    assert job.last_error and "timed out" in job.last_error
    assert job.locked_at is None
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_print_job_exhausted_marks_failed(db_session, monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("XPYUN_ENABLED", "true")
    monkeypatch.setenv("PRINTER_MODE", "local")
    monkeypatch.setenv("PRINTER_HOST", "192.168.1.200")
    get_settings.cache_clear()

    store, order = await _seed_paid_order(db_session)
    job = PrintJob(
        store_id=store.id,
        order_id=order.id,
        ticket_type="KITCHEN",
        content="<C>test</C><CUT>",
        content_hash="abc",
        status="PENDING",
        attempt_count=MAX_PRINT_ATTEMPTS - 1,
    )
    db_session.add(job)
    await db_session.flush()

    with patch("app.services.print_service.EscposClient.print_ticket", new_callable=AsyncMock) as mock_print:
        mock_print.side_effect = RuntimeError("still down")
        with pytest.raises(AppException):
            await PrintService.print_order(db_session, order.id, print_job_id=job.id)

    await db_session.refresh(job)
    assert job.status == "FAILED"
    assert job.attempt_count == MAX_PRINT_ATTEMPTS
    assert job.next_retry_at is None
    assert job.succeeded_at is None
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_claim_print_job_atomic(db_session):
    from datetime import timedelta

    from app.core.response import now_local

    store, order = await _seed_paid_order(db_session)
    job = PrintJob(
        store_id=store.id,
        order_id=order.id,
        ticket_type="KITCHEN",
        content="<C>retry</C><CUT>",
        content_hash="def",
        status="RETRYING",
        attempt_count=1,
        next_retry_at=now_local() - timedelta(seconds=1),
        last_error="prev",
    )
    db_session.add(job)
    await db_session.commit()

    claimed = await PrintService.claim_print_job(db_session, job.id, worker="test-worker")
    assert claimed is not None
    assert claimed.status == "PRINTING"
    assert claimed.attempt_count == 2
    assert claimed.locked_by == "test-worker"

    again = await PrintService.claim_print_job(db_session, job.id, worker="other")
    assert again is None

@pytest.mark.asyncio
async def test_duplicate_print_trigger_does_not_reset_or_resend_active_job(db_session, monkeypatch):
    """重复 Webhook/Outbox 触发不得重置正在执行的打印任务或再次出纸。"""
    from app.core.config import get_settings
    from app.core.response import now_local

    get_settings.cache_clear()
    monkeypatch.setenv("XPYUN_ENABLED", "true")
    monkeypatch.setenv("PRINTER_MODE", "local")
    monkeypatch.setenv("PRINTER_HOST", "192.168.1.200")
    get_settings.cache_clear()

    store, order = await _seed_paid_order(db_session)
    job = PrintJob(
        store_id=store.id,
        order_id=order.id,
        ticket_type="KITCHEN",
        content="<C>already printing</C><CUT>",
        content_hash="active-hash",
        status="PRINTING",
        attempt_count=1,
        locked_at=now_local(),
        locked_by="original-print-owner",
    )
    db_session.add(job)
    await db_session.commit()

    with patch("app.services.print_service.EscposClient.print_ticket", new_callable=AsyncMock) as mock_print:
        result = await PrintService.print_order(db_session, order.id)
        explicit_retry = await PrintService.print_order(
            db_session, order.id, print_job_id=job.id
        )
        mock_print.assert_not_awaited()

    await db_session.refresh(job)
    assert result["deduplicated"] is True
    assert explicit_retry["deduplicated"] is True
    assert result["print_job_id"] == job.id
    assert explicit_retry["print_job_id"] == job.id
    assert job.status == "PRINTING"
    assert job.locked_by == "original-print-owner"
    assert job.attempt_count == 1
    get_settings.cache_clear()


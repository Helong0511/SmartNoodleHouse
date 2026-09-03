"""厨房小票打印测试"""
import re
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.core.biz_no import BIZ_OT, generate_no
from app.models import Order, OrderItem, Store
from app.services.escpos_client import xpyun_markup_to_escpos
from app.services.print_service import (
    PrintService,
    TICKET_KITCHEN,
    _barcode_payload,
    build_ticket_content,
    OrderPrintContext,
)
from app.services.xpyun_client import xpyun_sign


def test_xpyun_sign_stable():
    sig = xpyun_sign("user@test.com", "secretkey", "1565417654")
    assert len(sig) == 40
    assert sig == sig.lower()
    assert sig == xpyun_sign("user@test.com", "secretkey", "1565417654")


def test_barcode_payload_from_order_no():
    no = "OTSH01A0120260902113045K7M2"
    assert _barcode_payload(no) == no
    assert len(_barcode_payload(no)) == 27


def test_build_ticket_contains_barcode_and_order_no():
    order = Order(
        id=1,
        order_no="OTSH01A0120260902113045K7M2",
        store_id=1,
        status="PAID",
        channel="WECHAT",
        dining_type="DINE_IN",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
    )
    item = OrderItem(
        id=10,
        order_id=1,
        product_id=1,
        product_name_snapshot="牛肉面",
        unit_price_snapshot=Decimal("18.00"),
        quantity=1,
        subtotal=Decimal("18.00"),
    )
    ctx = OrderPrintContext(
        order=order,
        store=Store(id=1, store_no="SH01", name="测试面馆"),
        table=None,
        delivery=None,
        items=[item],
        options_by_item={},
        no_cooking_by_product={},
    )
    content = build_ticket_content(ctx, ticket_type=TICKET_KITCHEN)
    assert "厨房制作单" in content
    assert order.order_no in content
    assert "<B2>x1</B2>" in content
    assert '<BARCODE t="CODE128"' in content
    assert _barcode_payload(order.order_no) in content
    assert "<CUT>" in content
    assert "<BR>" in content


@pytest.mark.asyncio
async def test_print_order_mock_when_not_configured(db_session, monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("XPYUN_ENABLED", "true")
    monkeypatch.setenv("ALLOW_PRINT_MOCK", "true")
    monkeypatch.setenv("XPYUN_USER", "")
    get_settings.cache_clear()

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
    )
    db_session.add(order)
    await db_session.flush()

    result = await PrintService.print_order(db_session, order.id)
    assert result is not None
    assert result.get("mock") is True
    get_settings.cache_clear()


def test_xpyun_markup_to_escpos_contains_cut_and_text():
    raw = "<C><B>厨房制作单</B></C><BR>牛肉面<BR><CUT>"
    data = xpyun_markup_to_escpos(raw)
    assert b"\x1dV\x01" in data
    assert "厨房制作单".encode("gbk") in data


@pytest.mark.asyncio
async def test_print_order_calls_local_escpos(db_session, monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("XPYUN_ENABLED", "true")
    monkeypatch.setenv("PRINTER_MODE", "local")
    monkeypatch.setenv("PRINTER_HOST", "192.168.1.200")
    monkeypatch.setenv("PRINTER_PORT", "9100")
    get_settings.cache_clear()

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

    with patch("app.services.print_service.EscposClient.print_ticket", new_callable=AsyncMock) as mock_print:
        mock_print.return_value = {"code": 0, "msg": "ok", "mode": "local"}
        result = await PrintService.print_order(db_session, order.id)
        assert result == {"code": 0, "msg": "ok", "mode": "local"}
        mock_print.assert_awaited_once()
        assert order.order_no in mock_print.call_args.args[0]

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_print_order_calls_xpyun_api(db_session, monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("XPYUN_ENABLED", "true")
    monkeypatch.setenv("PRINTER_MODE", "xpyun")
    monkeypatch.setenv("XPYUN_USER", "dev@example.com")
    monkeypatch.setenv("XPYUN_USER_KEY", "testkey")
    monkeypatch.setenv("XPYUN_PRINTER_SN", "XPY123456789A")
    get_settings.cache_clear()

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

    with patch("app.services.print_service.XpyunClient.print_ticket", new_callable=AsyncMock) as mock_print:
        mock_print.return_value = {"code": 0, "msg": "ok"}
        result = await PrintService.print_order(db_session, order.id)
        assert result == {"code": 0, "msg": "ok"}
        mock_print.assert_awaited_once()
        args, kwargs = mock_print.call_args
        assert args[0] == "XPY123456789A"
        assert order.order_no in args[1]
        assert re.search(r"<BARCODE[^>]*>.*</BARCODE>", args[1])
        assert kwargs.get("cutter") == 1

    get_settings.cache_clear()

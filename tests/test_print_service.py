"""厨房小票打印测试"""
import re
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.core.biz_no import BIZ_OT, generate_no
from app.models import DiningTable, Order, OrderItem, Store
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


def test_build_ticket_contains_barcode_and_order_no(monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("KITCHEN_TICKET_ORDER_NO", "barcode")
    monkeypatch.setenv("PRINTER_MODE", "local")
    get_settings.cache_clear()

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
        product_name_snapshot="绵阳米粉（牛肉）",
        unit_price_snapshot=Decimal("18.00"),
        quantity=1,
        subtotal=Decimal("18.00"),
    )
    ctx = OrderPrintContext(
        order=order,
        store=Store(id=1, store_no="SH01", name="测试面馆"),
        table=DiningTable(id=3, store_id=1, table_no="A02", table_name="2号桌", qr_token="t", status=1),
        delivery=None,
        items=[item],
        options_by_item={},
        no_cooking_by_product={},
    )
    content = build_ticket_content(ctx, ticket_type=TICKET_KITCHEN)
    assert "测试面馆 厨房制作单" in content
    assert "时间：" in content
    assert re.search(r"时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", content)
    assert "<B>堂食 2号桌</B>" in content
    meta = next(ln for ln in content.split("<BR>") if ln.startswith("时间："))
    assert meta.index("时间：") < meta.index("<B>堂食")
    # 中间有空格把时间与堂食左右撑开
    assert "  " in meta
    assert order.order_no in content
    assert "<B2>" in content and "x1" in content
    assert "绵阳米粉（牛肉）" in content
    assert content.split("<BR>")[0].startswith("<C><B>测试面馆 厨房制作单")
    name_line = [ln for ln in content.split("<BR>") if "x1" in ln and "绵阳米粉" in ln]
    assert name_line, content
    assert '<BARCODE t="CODE128"' in content
    assert 'w="2"' in content
    assert "<QR" not in content
    assert _barcode_payload(order.order_no) in content
    assert content.index("<BARCODE") < content.rindex(order.order_no)
    assert content.endswith("<CUT>")
    get_settings.cache_clear()


def test_kitchen_ticket_order_no_modes(monkeypatch):
    from app.core.config import get_settings

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
    ctx = OrderPrintContext(
        order=order,
        store=Store(id=1, store_no="SH01", name="测试面馆"),
        table=None,
        delivery=None,
        items=[],
        options_by_item={},
        no_cooking_by_product={},
    )

    get_settings.cache_clear()
    monkeypatch.setenv("KITCHEN_TICKET_ORDER_NO", "text")
    get_settings.cache_clear()
    text_ticket = build_ticket_content(ctx, ticket_type=TICKET_KITCHEN)
    assert order.order_no in text_ticket
    assert "<BARCODE" not in text_ticket
    assert "<QR" not in text_ticket

    monkeypatch.setenv("KITCHEN_TICKET_ORDER_NO", "off")
    get_settings.cache_clear()
    off_ticket = build_ticket_content(ctx, ticket_type=TICKET_KITCHEN)
    assert order.order_no not in off_ticket
    assert "<BARCODE" not in off_ticket
    get_settings.cache_clear()


def test_escpos_inline_b2_keeps_time_normal_size():
    raw = "时间：2026-09-05 14:12 <B2>堂食 2号桌</B2>"
    data = xpyun_markup_to_escpos(raw)
    # 先普通字号再倍高倍宽，最后恢复
    assert b"\x1d!\x00" in data
    assert b"\x1d!\x11" in data
    assert "时间：2026-09-05 14:12 ".encode("gbk") in data
    assert "堂食 2号桌".encode("gbk") in data


def test_kitchen_item_name_qty_same_line():
    from app.services.print_service import _kitchen_item_name_qty_line

    line = _kitchen_item_name_qty_line("绵阳米粉（牛肉）", 1)
    assert line.startswith("<B2>") and line.endswith("</B2>")
    assert "绵阳米粉（牛肉）" in line
    assert "x1" in line
    assert line.index("绵阳米粉") < line.index("x1")


def test_escpos_barcode_has_code128_prefix_and_height():
    from app.services.escpos_client import _code128_body, _code128_module_count

    no = "OTSH01A0120260902113045K7M2"
    raw = f'<C><BARCODE t="CODE128" w="2" h="64" p="0">{no}</BARCODE></C>'
    data = xpyun_markup_to_escpos(raw)
    assert b"\x1dh" in data  # GS h height
    assert b"\x1dw\x02" in data  # GS w = 2
    assert b"\x1dkI" in data  # GS k CODE128
    body = _code128_body(no)
    assert body.startswith(b"{B")
    assert b"{C" in body
    assert _code128_module_count(body) * 2 <= 560


def test_escpos_cut_feeds_after_barcode():
    raw = (
        '<C><BARCODE t="CODE128" w="2" h="64" p="0">OTSH01A0120260902113045K7M2</BARCODE></C><BR>'
        "<C>OTSH01A0120260902113045K7M2</C><BR>"
        "<CUT>"
    )
    data = xpyun_markup_to_escpos(raw)
    assert b"\x1dkI" in data
    assert b"\x1dV\x41" in data
    cut_at = data.rfind(b"\x1dV\x41")
    assert cut_at > 0
    assert data[cut_at - 2 : cut_at] == b"\n\n"
    assert data[cut_at + 3] == 18  # GS V 65 n，n=18


@pytest.mark.asyncio
async def test_print_order_mock_when_not_configured(db_session, monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("XPYUN_ENABLED", "true")
    monkeypatch.setenv("ALLOW_PRINT_MOCK", "true")
    monkeypatch.setenv("XPYUN_USER", "")
    monkeypatch.setenv("PRINTER_MODE", "local")
    monkeypatch.setenv("PRINTER_HOST", "")
    monkeypatch.setenv("XPYUN_PRINTER_SN", "")
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
    assert b"\x1dV\x41" in data  # 走纸后切（单次）
    assert data.count(b"\x1dV") == 1
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
        assert result["code"] == 0
        assert result["msg"] == "ok"
        assert result["mode"] == "local"
        assert result.get("print_job_id")
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
        assert result["code"] == 0
        assert result["msg"] == "ok"
        assert result.get("print_job_id")
        mock_print.assert_awaited_once()
        args, kwargs = mock_print.call_args
        assert args[0] == "XPY123456789A"
        assert order.order_no in args[1]
        assert re.search(r"<BARCODE[^>]*>.*</BARCODE>", args[1])
        assert kwargs.get("cutter") == 1

    get_settings.cache_clear()

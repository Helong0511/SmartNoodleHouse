"""顾客叫号屏 API 与展示字段。"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Order, Store
from app.services.customer_display import (
    CUSTOMER_STATUS_LABEL,
    build_display_item,
    is_in_store_order,
    pickup_code_from_order_no,
    pickup_fields,
    summarize_order_items,
)


def test_pickup_code_from_order_no():
    no = "OTSH01A0120260827173045K7M2"
    assert pickup_code_from_order_no(no) == "K7M2"
    assert pickup_code_from_order_no("SHORT") == "HORT"


def test_is_in_store_order():
    assert is_in_store_order(Order(channel="WECHAT"))
    assert not is_in_store_order(Order(channel="MEITUAN"))


def test_summarize_order_items():
    items = [
        SimpleNamespace(product_name_snapshot="经典肠旺面", quantity=1),
        SimpleNamespace(product_name_snapshot="卤蛋", quantity=2),
        SimpleNamespace(product_name_snapshot="经典肠旺面", quantity=1),
        SimpleNamespace(product_name_snapshot="酸梅汤", quantity=1),
        SimpleNamespace(product_name_snapshot="饮料", quantity=1),
    ]
    text, count = summarize_order_items(items, max_names=3)
    assert count == 6
    assert text.startswith("经典肠旺面×2、卤蛋×2、酸梅汤×1")
    assert "等6件" in text
    assert summarize_order_items([]) == ("", 0)


def test_build_display_item_dine_in():
    order = Order(
        id=1,
        order_no="OTSH01A0120260827173045K7M2",
        status="PREPARING",
        dining_type="DINE_IN",
        table_id=3,
        channel="WECHAT",
    )
    from app.models import DiningTable

    table = DiningTable(id=3, table_no="A01", table_name="A01")
    lines = [
        SimpleNamespace(product_name_snapshot="经典肠旺面", quantity=1),
        SimpleNamespace(product_name_snapshot="卤蛋", quantity=1),
    ]
    item = build_display_item(order, table, items=lines)
    assert item["pickup_label"] == "A01"
    assert item["pickup_sub"] == "堂食 · K7M2"
    assert item["pickup_code"] == "K7M2"
    assert item["items_summary"] == "经典肠旺面×1、卤蛋×1"
    assert item["item_count"] == 2
    assert item["status_label"] == CUSTOMER_STATUS_LABEL["PREPARING"]


def test_build_display_item_takeaway():
    order = Order(
        id=2,
        order_no="OTSH01TKW20260827173045ABCD",
        status="READY",
        dining_type="TAKEAWAY",
        table_id=None,
        channel="H5",
    )
    from datetime import datetime, timezone

    ready_at = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    item = build_display_item(
        order,
        None,
        ready_at=ready_at,
        items=[SimpleNamespace(product_name_snapshot="酸辣粉", quantity=2)],
    )
    assert item["pickup_label"] == pickup_code_from_order_no(order.order_no)
    assert item["pickup_sub"] == "打包自取"
    assert item["items_summary"] == "酸辣粉×2"
    assert item["status_label"] == "请取餐"
    # ready_at 走全局 serialize_dt：aware 时间统一转换为中国本地 +08:00
    assert item["ready_at"] == "2026-08-27T18:00:00+08:00"


def test_pickup_fields():
    from app.models import DiningTable

    order = Order(
        id=3,
        order_no="OTSH01A0120260827173045K7M2",
        status="READY",
        dining_type="DINE_IN",
        table_id=1,
        channel="WECHAT",
    )
    table = DiningTable(id=1, table_no="A01", table_name="A01")
    fields = pickup_fields(order, table, has_customer_display=True)
    assert fields["pickup_code"] == "K7M2"
    assert fields["pickup_label"] == "A01"
    assert fields["pickup_sub"] == "堂食 · K7M2"
    assert fields["show_pickup_banner"] is True
    assert fields["customer_status_label"] == "请取餐"
    assert fields["has_customer_display"] is True


@pytest.mark.asyncio
async def test_display_active_api(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from unittest.mock import patch

    from app.core.database import Base
    from app.core.config import get_settings
    from app.main import app

    display_tok = "test-display-token"
    # 强制叫号屏令牌，避免依赖本机 .env 是否已配置 DISPLAY_SCREEN_TOKEN
    real = get_settings()
    monkeypatch.setattr(real, "display_screen_token", display_tok)
    monkeypatch.setattr(real, "enable_customer_display", True)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        from app.models import OrderItem

        store = Store(id=1, store_no="SH01", name="测试店", status=1)
        db.add(store)
        db.add(
            Order(
                id=1,
                order_no="OTSH01A0120260827173045K7M2",
                store_id=1,
                status="READY",
                dining_type="DINE_IN",
                table_id=None,
                channel="WECHAT",
                payment_status="PAID",
                ordered_at=datetime.now(timezone.utc),
                paid_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            OrderItem(
                id=1,
                order_id=1,
                product_id=1,
                product_name_snapshot="经典肠旺面",
                unit_price_snapshot=18,
                quantity=1,
                subtotal=18,
            )
        )
        db.add(
            Order(
                id=2,
                order_no="OTSH01DLV20260827173045WXYZ",
                store_id=1,
                status="READY",
                dining_type="TAKEAWAY",
                channel="MEITUAN",
                payment_status="PAID",
            )
        )
        await db.commit()

    from app.core import database as db_mod

    orig = db_mod.AsyncSessionLocal
    db_mod.AsyncSessionLocal = Session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(
                "/api/orders/display/active",
                params={"store_id": 1, "token": display_tok},
            )
            assert res.status_code == 200
            body = res.json()
            assert body["code"] == 0
            data = body["data"]
            assert data["store"]["name"] == "测试店"
            assert len(data["ready"]) == 1
            assert data["ready"][0]["pickup_code"] == "K7M2"
            assert data["ready"][0]["items_summary"] == "经典肠旺面×1"
            assert data["ready"][0]["item_count"] == 1
            assert len(data["preparing"]) == 0
    finally:
        db_mod.AsyncSessionLocal = orig
        await engine.dispose()

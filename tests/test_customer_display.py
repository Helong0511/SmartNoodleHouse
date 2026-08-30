"""顾客叫号屏 API 与展示字段。"""
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Order, Store
from app.services.customer_display import (
    CUSTOMER_STATUS_LABEL,
    build_display_item,
    is_in_store_order,
    pickup_code_from_order_no,
    pickup_fields,
)


def test_pickup_code_from_order_no():
    no = "OTSH01A0120260827173045K7M2"
    assert pickup_code_from_order_no(no) == "K7M2"
    assert pickup_code_from_order_no("SHORT") == "HORT"


def test_is_in_store_order():
    assert is_in_store_order(Order(channel="WECHAT"))
    assert not is_in_store_order(Order(channel="MEITUAN"))


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
    item = build_display_item(order, table)
    assert item["pickup_label"] == "A01"
    assert item["pickup_sub"] == "堂食"
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
    item = build_display_item(order, None, ready_at=ready_at)
    assert item["pickup_label"] == pickup_code_from_order_no(order.order_no)
    assert item["pickup_sub"] == "打包自取"
    assert item["status_label"] == "请取餐"
    assert item["ready_at"] == ready_at.isoformat()


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
    fields = pickup_fields(order, table)
    assert fields["pickup_code"] == "K7M2"
    assert fields["pickup_label"] == "A01"
    assert fields["show_pickup_banner"] is True
    assert fields["customer_status_label"] == "请取餐"


@pytest.mark.asyncio
async def test_display_active_api():
    from httpx import ASGITransport, AsyncClient

    from app.core.database import Base
    from app.main import app

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
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
            res = await client.get("/api/v1/orders/display/active", params={"store_id": 1})
            assert res.status_code == 200
            body = res.json()
            assert body["code"] == 0
            data = body["data"]
            assert data["store"]["name"] == "测试店"
            assert len(data["ready"]) == 1
            assert data["ready"][0]["pickup_code"] == "K7M2"
            assert len(data["preparing"]) == 0
    finally:
        db_mod.AsyncSessionLocal = orig
        await engine.dispose()

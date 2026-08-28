"""桌台 token 解析与下单门店一致性（需 MySQL 种子）。"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import Customer, DiningTable, Product
from app.services.customer_service import OrderCreateService
from app.services.table_resolve import resolve_dining_table

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需 MySQL 种子数据，设置 RUN_INTEGRATION=1 后运行",
)


@pytest.mark.asyncio
async def test_qr_token_ignores_mismatched_store_id():
    async with AsyncSessionLocal() as db:
        table = (await db.execute(select(DiningTable).limit(1))).scalars().first()
        assert table is not None
        resolved = await resolve_dining_table(db, table.qr_token, store_id=99999)
        assert resolved is not None
        assert resolved.id == table.id
        assert int(resolved.store_id) == int(table.store_id)


@pytest.mark.asyncio
async def test_create_order_with_wrong_store_id_but_valid_token():
    async with AsyncSessionLocal() as db:
        cust = (await db.execute(select(Customer).limit(1))).scalars().first()
        table = (await db.execute(select(DiningTable).limit(1))).scalars().first()
        prod = (
            await db.execute(select(Product).where(Product.status == "ON_SALE").limit(1))
        ).scalars().first()
        assert cust and table and prod

        order = await OrderCreateService.create_order(
            db,
            store_id=99999,
            table_token=table.qr_token,
            customer_id=cust.id,
            items=[{"product_id": prod.id, "quantity": 1, "option_ids": []}],
            channel="WECHAT",
            idempotency_key=f"test_{time.time()}",
            need_pack=False,
            dining_type="DINE_IN",
        )
        assert int(order.store_id) == int(table.store_id)
        assert order.table_id == table.id
        await db.rollback()

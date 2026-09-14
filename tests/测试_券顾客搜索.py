"""发券顾客搜索测试。"""
import os

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import Customer
from app.services.customer_service import CustomerService


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="需 MySQL 种子数据，设置 RUN_INTEGRATION=1 后运行")
async def test_search_active_customers_by_nickname():
    async with AsyncSessionLocal() as db:
        sample = (
            await db.execute(
                select(Customer).where(Customer.customer_no == "CU000000020260801100001S2C3")
            )
        ).scalar_one_or_none()
        if not sample or not sample.nickname:
            pytest.skip("种子顾客不存在或无昵称")
        keyword = sample.nickname[:2]
        rows, total = await CustomerService.search_active_customers(db, keyword, page_size=20)
        ids = {c.id for c in rows}
        assert sample.id in ids, f"昵称关键词「{keyword}」应能搜到 {sample.nickname}"
        assert total >= 1


@pytest.mark.asyncio
async def test_keyword_condition_empty():
    assert CustomerService.keyword_condition("") is None
    assert CustomerService.keyword_condition("  ") is None

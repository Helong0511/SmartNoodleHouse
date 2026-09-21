"""查询 alembic 版本与索引状态。"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
import sys
sys.path.insert(0, "e:/SmartNoodleHouse/backend")
from app.core.config import get_settings


async def main():
    e = create_async_engine(get_settings().database_url)
    try:
        async with e.connect() as c:
            r = await c.execute(text("SELECT version_num FROM alembic_version"))
            print("alembic:", r.fetchall())
            # financial_ledger 索引
            r = await c.execute(text(
                "SELECT DISTINCT INDEX_NAME FROM information_schema.statistics "
                "WHERE table_schema='smart_noodle_house' AND table_name='financial_ledger'"
            ))
            print("fin idx:", [row[0] for row in r.fetchall()])
            # delivery_orders 4 列 NULLABLE
            r = await c.execute(text(
                "SELECT COLUMN_NAME, IS_NULLABLE FROM information_schema.columns "
                "WHERE table_schema='smart_noodle_house' AND table_name='delivery_orders' "
                "AND COLUMN_NAME IN ('platform_fee','packing_fee','delivery_fee','refund_status')"
            ))
            for row in r.fetchall():
                print("delivery_orders:", row)
            # customer_coupons.client_request_id 单列 UNIQUE
            r = await c.execute(text(
                "SELECT DISTINCT INDEX_NAME FROM information_schema.statistics "
                "WHERE table_schema='smart_noodle_house' AND table_name='customer_coupons' "
                "AND COLUMN_NAME='client_request_id'"
            ))
            print("customer_coupons.client_request_id idx:", [row[0] for row in r.fetchall()])
            # tables.store_id+table_no
            r = await c.execute(text(
                "SELECT DISTINCT INDEX_NAME FROM information_schema.statistics "
                "WHERE table_schema='smart_noodle_house' AND table_name='tables' "
                "AND COLUMN_NAME IN ('store_id','table_no')"
            ))
            print("tables idx:", [row[0] for row in r.fetchall()])
            # delivery_refunds (platform, platform_refund_id)
            r = await c.execute(text(
                "SELECT DISTINCT INDEX_NAME FROM information_schema.statistics "
                "WHERE table_schema='smart_noodle_house' AND table_name='delivery_refunds' "
                "AND COLUMN_NAME IN ('platform','platform_refund_id')"
            ))
            print("delivery_refunds idx:", [row[0] for row in r.fetchall()])
    finally:
        await e.dispose()


asyncio.run(main())

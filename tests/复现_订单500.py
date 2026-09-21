"""复现下单 500：直接调 OrderCreateService + HTTP。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import create_customer_token
from app.models import Customer, DiningTable, Product
from app.services.coupon_service import CouponService
from app.services.customer_service import OrderCreateService

CUSTOMER_NO = "CU000000020260801100002S2C4"  # 银卡/金卡测试顾客


async def service_repro():
    async with AsyncSessionLocal() as db:
        cust = (
            await db.execute(select(Customer).where(Customer.customer_no == CUSTOMER_NO))
        ).scalar_one_or_none()
        table = (
            await db.execute(select(DiningTable).where(DiningTable.qr_token == "1001"))
        ).scalar_one_or_none()
        prod = (await db.execute(select(Product).where(Product.name == "牛肉面"))).scalar_one_or_none()
        coupons = await CouponService.list_available(db, cust.id)
        coupon_id = coupons[0]["id"] if coupons else None
        print("coupon_id", coupon_id, coupons[0] if coupons else None)
        try:
            order = await OrderCreateService.create_order(
                db,
                store_id=table.store_id,
                table_token=table.qr_token,
                customer_id=cust.id,
                items=[{"product_id": prod.id, "quantity": 6, "option_ids": []}],
                channel="WECHAT",
                customer_coupon_id=coupon_id,
                idempotency_key=f"repro_{__import__('time').time()}",
            )
            await db.commit()
            print("SERVICE OK", order.order_no, order.total_amount, order.discount_amount, order.coupon_discount)
        except Exception as e:
            import traceback

            print("SERVICE FAIL", type(e).__name__, e)
            traceback.print_exc()
            await db.rollback()
        return cust, table, prod, coupon_id


async def http_repro(cust, table, prod, coupon_id):
    token = create_customer_token(cust.id, "repro")
    payload = {
        "store_id": table.store_id,
        "table_token": table.qr_token,
        "channel": "WECHAT",
        "need_pack": False,
        "dining_type": "DINE_IN",
        "customer_coupon_id": coupon_id,
        "idempotency_key": f"http_repro_{__import__('time').time()}",
        "items": [{"product_id": prod.id, "quantity": 6, "option_ids": []}],
    }
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as client:
        r = await client.post(
            "/api/orders",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        print("HTTP", r.status_code, r.text[:2000])


async def main():
    cust, table, prod, coupon_id = await service_repro()
    if cust and table and prod:
        await http_repro(cust, table, prod, coupon_id)


if __name__ == "__main__":
    asyncio.run(main())

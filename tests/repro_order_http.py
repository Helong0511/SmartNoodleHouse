"""HTTP 复现 POST /orders。"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import create_customer_token
from app.models import Customer, DiningTable, Product
from app.services.coupon_service import CouponService


async def main():
    async with AsyncSessionLocal() as db:
        cust = (
            await db.execute(select(Customer).where(Customer.customer_no == "CU000000020260801100001S2C3"))
        ).scalar_one()
        cust_id = cust.id
        table = (
            await db.execute(select(DiningTable).where(DiningTable.qr_token == "1001"))
        ).scalar_one()
        prod = (await db.execute(select(Product).where(Product.product_no == "P001"))).scalar_one()
        coupons = await CouponService.list_available(db, cust.id)
        coupon_id = coupons[0]["id"] if coupons else None
        store_id = table.store_id
        table_token = table.qr_token
        product_id = prod.id

    token = create_customer_token(cust_id, "repro")
    payload = {
        "store_id": store_id,
        "table_token": table_token,
        "channel": "WECHAT",
        "need_pack": False,
        "dining_type": "DINE_IN",
        "customer_coupon_id": coupon_id,
        "idempotency_key": f"http_repro_{time.time()}",
        "items": [{"product_id": product_id, "quantity": 6, "option_ids": [2, 6, 10]}],
    }
    print("payload coupon", coupon_id)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=30) as client:
        r = await client.post(
            "/api/v1/orders",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        print("status", r.status_code)
        print(r.text[:3000])


if __name__ == "__main__":
    asyncio.run(main())

"""顾客历史订单列表与再来一单（复购进购物车）冒烟测试。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.core.security import create_customer_token
from app.core.database import AsyncSessionLocal
from sqlalchemy import select
from app.models import Customer, Order


async def main() -> None:
    async with AsyncSessionLocal() as db:
        cust = (await db.execute(select(Customer).where(Customer.status == 1).limit(1))).scalar_one_or_none()
        if not cust:
            print("SKIP: no customer")
            return
        order = (
            await db.execute(
                select(Order).where(Order.customer_id == cust.id).order_by(Order.id.desc()).limit(1)
            )
        ).scalar_one_or_none()
        token = create_customer_token(cust.id, "wechat")

    headers = {"Authorization": f"Bearer {token}"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/orders/mine", headers=headers)
        body = r.json()
        print("mine", r.status_code, body.get("code"), "total=", (body.get("data") or {}).get("total"))
        assert r.status_code == 200 and body.get("code") == 0

        if order:
            r2 = await client.post(f"/api/orders/{order.id}/reorder", headers=headers)
            body2 = r2.json()
            print(
                "reorder",
                r2.status_code,
                body2.get("code"),
                body2.get("message"),
                "cart=",
                len(((body2.get("data") or {}).get("cart_items") or [])),
            )
            if r2.status_code == 200 and body2.get("code") == 0:
                for it in (body2.get("data") or {}).get("cart_items") or []:
                    assert "no_member_discount" in it, "reorder cart_items should include no_member_discount"
        else:
            print("SKIP reorder: no order for customer", cust.id)


if __name__ == "__main__":
    asyncio.run(main())

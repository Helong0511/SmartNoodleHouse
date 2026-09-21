"""退款扣积分：对真实库走一笔退款并校验顾客积分变化（开发/测试环境）。"""
from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import Customer, Payment

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="会连接并修改真实 MySQL/API 数据；设置 RUN_INTEGRATION=1 后单独运行",
)

API = "http://127.0.0.1:8000/api"


async def _login(client: httpx.AsyncClient) -> str:
    r = await client.post(f"{API}/auth/login", json={"username": "admin", "password": "admin123"})
    r.raise_for_status()
    body = r.json()
    assert body.get("code") == 0, body
    return body["data"]["access_token"]


async def _find_refundable_payment() -> tuple[str, int, Decimal]:
    """返回 (payment_no, customer_id, refundable_amount)。"""
    from app.models import Order, PaymentRefund
    from app.services.payment_service import PaymentService
    from sqlalchemy import func

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Payment, Order.customer_id)
                .join(Order, Order.id == Payment.order_id)
                .where(
                    Payment.status.in_(("PAID", "PARTIAL_REFUND")),
                    Order.customer_id.isnot(None),
                )
                .order_by(Payment.id.desc())
            )
        ).all()
        for payment, customer_id in rows:
            customer = await db.get(Customer, int(customer_id))
            if not customer or int(customer.points or 0) <= 0:
                continue
            remain = await PaymentService.refundable_amount(db, payment)
            if remain >= Decimal("5.00"):
                return payment.payment_no, int(customer_id), remain
    raise RuntimeError("未找到可退款且顾客有积分的支付单，请先下单支付或执行种子数据")


@pytest.mark.asyncio
async def test_refund_deducts_customer_points_via_api():
    """发起部分退款，断言积分按 1 元 = 1 积分扣减。"""
    payment_no, customer_id, remain = await _find_refundable_payment()
    refund_amount = min(Decimal("10.00"), remain)
    assert refund_amount > 0

    async with AsyncSessionLocal() as db:
        before = await db.get(Customer, customer_id)
        assert before is not None
        points_before = int(before.points or 0)
        level_before = before.member_level_id

    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _login(client)
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.post(
            f"{API}/payments/{payment_no}/refund",
            headers=headers,
            json={"amount": str(refund_amount), "reason": "自动化测试：退款扣积分"},
        )
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload.get("code") == 0, payload
        assert payload["data"]["status"] == "SUCCESS", payload

        cr = await client.get(f"{API}/customers/{customer_id}", headers=headers)
        assert cr.status_code == 200, cr.text
        profile = cr.json()["data"]

    points_after = int(profile.get("member_points") or profile.get("points") or 0)
    level_after = profile.get("member_level_id")

    expected_after = max(0, points_before - int(refund_amount))
    assert points_after == expected_after, (
        f"积分未按退款扣减：退款 ¥{refund_amount}，"
        f"前 {points_before} → 后 {points_after}，期望 {expected_after}"
    )

    print(
        f"\n[refund points] payment={payment_no} customer={customer_id} "
        f"refund={refund_amount} points {points_before}->{points_after} "
        f"level {level_before}->{level_after}"
    )


if __name__ == "__main__":
    asyncio.run(test_refund_deducts_customer_points_via_api())

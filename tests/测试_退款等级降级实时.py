"""退款扣积分触发会员降级：开发库 live 测试。"""
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
from app.models import Customer, MemberLevel, Order, Payment
from app.services.customer_service import CustomerService
from app.services.payment_service import PaymentService

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="会连接并修改真实 MySQL/API 数据；设置 RUN_INTEGRATION=1 后单独运行",
)

API = "http://127.0.0.1:8000/api"

# 与本地测试种子/后台可改门槛一致（见 sql/data_test.sql 与会员等级配置）
GOLD_MIN = 300
SILVER_MIN = 120
LEVEL_REGULAR = 1
LEVEL_SILVER = 2
LEVEL_GOLD = 3


async def _login(client: httpx.AsyncClient) -> str:
    r = await client.post(f"{API}/auth/login", json={"username": "admin", "password": "admin123"})
    r.raise_for_status()
    body = r.json()
    assert body.get("code") == 0, body
    return body["data"]["access_token"]


async def _refund_via_api(client: httpx.AsyncClient, token: str, payment_no: str, amount: Decimal) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.post(
        f"{API}/payments/{payment_no}/refund",
        headers=headers,
        json={"amount": str(amount), "reason": "自动化测试：退款降级"},
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload.get("code") == 0, payload
    assert payload["data"]["status"] == "SUCCESS", payload


async def _customer_state(customer_id: int) -> tuple[int, int | None]:
    async with AsyncSessionLocal() as db:
        customer = await db.get(Customer, customer_id)
        assert customer is not None
        return int(customer.points or 0), customer.member_level_id


async def _wait_customer_state(
    customer_id: int,
    *,
    expected_points: int,
    expected_level: int,
    attempts: int = 10,
) -> tuple[int, int | None]:
    for _ in range(attempts):
        points, level = await _customer_state(customer_id)
        if points == expected_points and level == expected_level:
            return points, level
        await asyncio.sleep(0.2)
    points, level = await _customer_state(customer_id)
    assert points == expected_points, f"points {points} != {expected_points}"
    assert level == expected_level, f"level {level} != {expected_level}"
    return points, level


async def _find_payment_for_customer(customer_id: int, min_amount: Decimal) -> str:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Payment)
                .join(Order, Order.id == Payment.order_id)
                .where(
                    Order.customer_id == customer_id,
                    Payment.status.in_(("PAID", "PARTIAL_REFUND")),
                )
                .order_by(Payment.amount.desc())
            )
        ).scalars().all()
        for payment in rows:
            remain = await PaymentService.refundable_amount(db, payment)
            if remain >= min_amount:
                return payment.payment_no
    raise RuntimeError(f"顾客 {customer_id} 没有可退 >= {min_amount} 的支付单")


async def _load_level_thresholds() -> tuple[int, int]:
    async with AsyncSessionLocal() as db:
        levels = list(
            (await db.execute(select(MemberLevel).order_by(MemberLevel.min_points))).scalars().all()
        )
        silver = next((int(l.min_points) for l in levels if l.id == LEVEL_SILVER), SILVER_MIN)
        gold = next((int(l.min_points) for l in levels if l.id == LEVEL_GOLD), GOLD_MIN)
        return silver, gold


@pytest.mark.asyncio
async def test_refund_downgrades_member_level_gold_to_silver_to_regular():
    """
    将测试顾客积分设为金卡门槛+10，依次退款：
    1) 扣 15 分 -> 银卡（低于金卡门槛）
    2) 再扣至银卡门槛以下 -> 普通会员
    """
    customer_id = 4
    silver_min, gold_min = await _load_level_thresholds()
    setup_points = gold_min + 10  # 310 when gold=300

    async with AsyncSessionLocal() as db:
        customer = await db.get(Customer, customer_id)
        assert customer is not None, "测试顾客不存在"
        customer.points = setup_points
        customer.member_level_id = LEVEL_GOLD
        await CustomerService.recalculate_member_level(db, customer)
        await db.commit()
        assert customer.member_level_id == LEVEL_GOLD

    payment_no = await _find_payment_for_customer(customer_id, Decimal("200.00"))

    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await _login(client)

        # 金卡 -> 银卡
        refund1 = Decimal("15.00")
        await _refund_via_api(client, token, payment_no, refund1)
        expected_pts_1 = setup_points - int(refund1)
        pts_1, lvl_1 = await _wait_customer_state(
            customer_id, expected_points=expected_pts_1, expected_level=LEVEL_SILVER
        )
        assert pts_1 < gold_min and pts_1 >= silver_min

        # 银卡 -> 普通
        refund2 = Decimal(str(expected_pts_1 - silver_min + 1))
        await _refund_via_api(client, token, payment_no, refund2)
        expected_pts_2 = expected_pts_1 - int(refund2)
        pts_2, lvl_2 = await _wait_customer_state(
            customer_id, expected_points=expected_pts_2, expected_level=LEVEL_REGULAR
        )
        assert pts_2 < silver_min

        # API 侧也应一致
        headers = {"Authorization": f"Bearer {token}"}
        p2 = (await client.get(f"{API}/customers/{customer_id}", headers=headers)).json()["data"]
        assert int(p2.get("member_points") or 0) == expected_pts_2
        assert p2.get("member_level_id") == LEVEL_REGULAR

    print(
        f"\n[refund downgrade] customer={customer_id} "
        f"points {setup_points}->{expected_pts_1}->{expected_pts_2} "
        f"level {LEVEL_GOLD}->{LEVEL_SILVER}->{LEVEL_REGULAR}"
    )


if __name__ == "__main__":
    asyncio.run(test_refund_downgrades_member_level_gold_to_silver_to_regular())

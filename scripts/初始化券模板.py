"""种子：优惠券/代金券模板（幂等，按 name 去重）。

注册赠券名称须与 .env WELCOME_COUPON_NAME 一致（默认「新客满30减5」）。

用法:
  python scripts/seed_coupon_templates.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import CouponTemplate

TEMPLATES = [
    # name, coupon_type, discount_value, min_order_amount, valid_days, status
    ("新客满30减5", "FIXED", Decimal("5.00"), Decimal("30.00"), 30, 1),
    ("满50享打9折", "PERCENT", Decimal("0.9000"), Decimal("50.00"), 30, 1),
    ("10元代金券", "VOUCHER", Decimal("10.00"), Decimal("0.00"), 30, 1),
]


async def main() -> None:
    async with AsyncSessionLocal() as db:
        created = 0
        for name, coupon_type, discount_value, min_order_amount, valid_days, status in TEMPLATES:
            row = (
                await db.execute(select(CouponTemplate).where(CouponTemplate.name == name))
            ).scalar_one_or_none()
            if row:
                row.coupon_type = coupon_type
                row.discount_value = discount_value
                row.min_order_amount = min_order_amount
                row.valid_days = valid_days
                row.status = status
                continue
            db.add(
                CouponTemplate(
                    name=name,
                    coupon_type=coupon_type,
                    discount_value=discount_value,
                    min_order_amount=min_order_amount,
                    valid_days=valid_days,
                    status=status,
                )
            )
            created += 1
        await db.commit()
        total = len((await db.execute(select(CouponTemplate))).scalars().all())
        print(f"coupon_templates: created={created}, total={total}")


if __name__ == "__main__":
    asyncio.run(main())

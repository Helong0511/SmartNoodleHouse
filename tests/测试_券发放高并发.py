"""优惠券发放高并发：多会话同时发同一顾客/同一幂等键，验证不重复、库存不超卖。

本地默认 SQLite；Ubuntu / CI MySQL 时设置 TEST_DATABASE_URL 即可跑同一套断言。
"""
from __future__ import annotations

import asyncio
import itertools
import os
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.core.exceptions import AppException
from app.models import CouponTemplate, Customer, CustomerCoupon
from app.services.coupon_service import CouponService

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
_IS_SQLITE = TEST_DATABASE_URL.startswith("sqlite")


def _customer(cid: int, no: str) -> Customer:
    return Customer(
        id=cid,
        customer_no=no,
        nickname=f"并发{cid}",
        status=1,
        points=0,
    )


def _template(
    tid: int,
    *,
    remaining: int | None,
    total_quota: int | None = None,
    name: str = "并发测试券",
) -> CouponTemplate:
    return CouponTemplate(
        id=tid,
        name=name,
        coupon_type="FIXED",
        discount_value=Decimal("5"),
        min_order_amount=Decimal("20"),
        valid_days=30,
        status=1,
        total_quota=total_quota if total_quota is not None else remaining,
        remaining=remaining,
    )


def _patch_sqlite_ids(session: AsyncSession, start: int) -> None:
    if not _IS_SQLITE:
        return
    ids = itertools.count(start)
    real_add = session.add

    def _add(obj):
        if isinstance(obj, CustomerCoupon) and getattr(obj, "id", None) is None:
            obj.id = next(ids)
        return real_add(obj)

    session.add = _add  # type: ignore[method-assign]


@pytest_asyncio.fixture
async def concurrent_engine():
    """独立引擎：多会话并发发券；SQLite 用 file 库才能跨连接看到同一数据。"""
    if _IS_SQLITE:
        url = "sqlite+aiosqlite:///file:coupon_conc?mode=memory&cache=shared&uri=true"
    else:
        url = TEST_DATABASE_URL
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_unit_client_request_ids_split_for_qty():
    assert CouponService._unit_client_request_ids("mkt:1:2:3", 1) == ["mkt:1:2:3"]
    assert CouponService._unit_client_request_ids("REQ", 3) == ["REQ:1", "REQ:2", "REQ:3"]


@pytest.mark.asyncio
async def test_concurrent_same_idempotency_key_issues_once(concurrent_engine):
    """N 个会话同时用同一 client_request_id 发券 → 只落 1 张，库存只扣 1。"""
    Session = async_sessionmaker(concurrent_engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        db.add_all([_customer(501, "CU_CONC_501"), _template(501, remaining=10, total_quota=10)])
        await db.commit()

    n = 16
    barrier = asyncio.Barrier(n)

    async def _one(worker: int) -> list[int]:
        async with Session() as db:
            _patch_sqlite_ids(db, 50_000 + worker * 100)
            await barrier.wait()
            with patch(
                "app.services.coupon_service.generate_no",
                side_effect=lambda p, w=worker: f"{p}C{w:02d}{id(db) % 100000}",
            ):
                try:
                    issued = await CouponService.issue_by_template_id(
                        db,
                        501,
                        501,
                        quantity=1,
                        client_request_id="CONC-WELCOME-501",
                    )
                    await db.commit()
                    return [int(c.id) for c in issued]
                except AppException as exc:
                    await db.rollback()
                    if exc.code in (40070, 50020):
                        return []
                    raise

    results = await asyncio.gather(*[_one(i) for i in range(n)])
    flat = [cid for ids in results for cid in ids]
    assert flat, "至少一次发券成功"
    assert len(set(flat)) == 1, f"同幂等键应只产生一张券，实际 ids={flat}"

    async with Session() as db:
        cnt = int(
            await db.scalar(
                select(func.count())
                .select_from(CustomerCoupon)
                .where(
                    CustomerCoupon.customer_id == 501,
                    CustomerCoupon.template_id == 501,
                )
            )
            or 0
        )
        tmpl = await db.get(CouponTemplate, 501)
        assert cnt == 1
        assert int(tmpl.remaining) == 9


@pytest.mark.asyncio
async def test_concurrent_quota_never_oversells(concurrent_engine):
    """库存 remaining=5，20 路并发各发 1 张 → 最多 5 张，remaining 最终为 0。"""
    Session = async_sessionmaker(concurrent_engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        db.add_all([_customer(502, "CU_CONC_502"), _template(502, remaining=5, total_quota=5)])
        await db.commit()

    n = 20
    barrier = asyncio.Barrier(n)

    async def _one(worker: int) -> int:
        async with Session() as db:
            _patch_sqlite_ids(db, 60_000 + worker * 100)
            await barrier.wait()
            with patch(
                "app.services.coupon_service.generate_no",
                side_effect=lambda p: f"{p}Q{worker:02d}{id(db) % 10000}",
            ):
                try:
                    issued = await CouponService.issue_by_template_id(
                        db,
                        502,
                        502,
                        quantity=1,
                        client_request_id=f"CONC-QUOTA-502-{worker}",
                    )
                    await db.commit()
                    return len(issued)
                except AppException as exc:
                    await db.rollback()
                    if exc.code == 40070:
                        return 0
                    raise

    wins = await asyncio.gather(*[_one(i) for i in range(n)])
    assert sum(wins) == 5

    async with Session() as db:
        cnt = int(
            await db.scalar(
                select(func.count())
                .select_from(CustomerCoupon)
                .where(CustomerCoupon.template_id == 502)
            )
            or 0
        )
        tmpl = await db.get(CouponTemplate, 502)
        assert cnt == 5
        assert int(tmpl.remaining) == 0


@pytest.mark.asyncio
async def test_batch_qty_with_idempotency_key_no_unique_clash(concurrent_engine):
    """quantity=3 + 同一 client_request_id：分键写入 3 张；重放返回同一批。"""
    Session = async_sessionmaker(concurrent_engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        db.add_all(
            [_customer(503, "CU_CONC_503"), _template(503, remaining=None, total_quota=None)]
        )
        await db.commit()

    async with Session() as db:
        _patch_sqlite_ids(db, 70_000)
        nos = itertools.count(1)
        with patch(
            "app.services.coupon_service.generate_no",
            side_effect=lambda p: f"{p}B{next(nos):04d}",
        ):
            first = await CouponService.issue_by_template_id(
                db, 503, 503, quantity=3, client_request_id="BATCH-503"
            )
            await db.commit()
        assert [c.client_request_id for c in first] == ["BATCH-503:1", "BATCH-503:2", "BATCH-503:3"]

    async with Session() as db:
        again = await CouponService.issue_by_template_id(
            db, 503, 503, quantity=3, client_request_id="BATCH-503"
        )
        await db.commit()
        assert {int(c.id) for c in again} == {int(c.id) for c in first}

    async with Session() as db:
        cnt = int(
            await db.scalar(
                select(func.count())
                .select_from(CustomerCoupon)
                .where(CustomerCoupon.customer_id == 503)
            )
            or 0
        )
        assert cnt == 3


@pytest.mark.asyncio
async def test_welcome_key_concurrent_no_duplicate(concurrent_engine):
    """欢迎券 WELCOME:{cid}:{tid} 双飞：issue_from_template 只成功一张。"""
    Session = async_sessionmaker(concurrent_engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        db.add_all(
            [
                _customer(504, "CU_CONC_504"),
                _template(504, remaining=20, total_quota=20, name="新客满20减3"),
            ]
        )
        await db.commit()

    barrier = asyncio.Barrier(12)

    async def _one(worker: int) -> int:
        async with Session() as db:
            _patch_sqlite_ids(db, 80_000 + worker * 50)
            await barrier.wait()
            with patch(
                "app.services.coupon_service.generate_no",
                side_effect=lambda p: f"{p}W{worker}",
            ):
                cc = await CouponService.issue_from_template(db, 504, "新客满20减3")
                await db.commit()
                return 1 if cc is not None else 0

    wins = await asyncio.gather(*[_one(i) for i in range(12)])
    assert sum(wins) == 1

    async with Session() as db:
        cnt = int(
            await db.scalar(
                select(func.count())
                .select_from(CustomerCoupon)
                .where(CustomerCoupon.customer_id == 504)
            )
            or 0
        )
        tmpl = await db.get(CouponTemplate, 504)
        assert cnt == 1
        assert int(tmpl.remaining) == 19
        key = (
            await db.execute(
                select(CustomerCoupon.client_request_id).where(CustomerCoupon.customer_id == 504)
            )
        ).scalar_one()
        assert key == "WELCOME:504:504"

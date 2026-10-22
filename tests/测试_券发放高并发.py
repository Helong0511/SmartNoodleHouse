"""优惠券发放高并发：幂等键 / 库存原子扣减 / 欢迎券防重。

- 默认可在 SQLite 上跑：分键、重放幂等、串行压力。
- 真实多会话并行压测需 MySQL 8（Ubuntu 生产同款），标记 ``mysql``，
  设置 ``TEST_DATABASE_URL`` 后执行：
  ``pytest tests/测试_券发放高并发.py -m mysql -q``
"""
from __future__ import annotations

import asyncio
import itertools
import os
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.core.exceptions import AppException
from app.models import CouponTemplate, Customer, CustomerCoupon
from app.services.coupon_service import CouponService

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
_IS_MYSQL = "mysql" in TEST_DATABASE_URL


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
    if _IS_MYSQL:
        return
    ids = itertools.count(start)
    real_add = session.add

    def _add(obj):
        if isinstance(obj, CustomerCoupon) and getattr(obj, "id", None) is None:
            obj.id = next(ids)
        return real_add(obj)

    session.add = _add  # type: ignore[method-assign]


@pytest_asyncio.fixture
async def mem_db():
    """单连接内存库：分键与重放幂等。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        yield db
    await engine.dispose()


@pytest_asyncio.fixture
async def mysql_engine():
    if not _IS_MYSQL:
        pytest.skip("需要 TEST_DATABASE_URL=mysql…（Ubuntu/CI MySQL job）")
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, pool_size=20, max_overflow=10)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        # 只清本文件用到的表行，避免 drop 影响并行 mysql job
        for tbl in ("customer_coupons", "coupon_templates", "customers"):
            await conn.execute(text(f"DELETE FROM {tbl} WHERE id BETWEEN 500 AND 599"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_unit_client_request_ids_split_for_qty():
    assert CouponService._unit_client_request_ids("mkt:1:2:3", 1) == ["mkt:1:2:3"]
    assert CouponService._unit_client_request_ids("REQ", 3) == ["REQ:1", "REQ:2", "REQ:3"]


@pytest.mark.asyncio
async def test_batch_qty_with_idempotency_key_no_unique_clash(mem_db: AsyncSession):
    """quantity=3 + 同一 client_request_id：分键写入；重放返回同一批。"""
    db = mem_db
    db.add_all([_customer(503, "CU_CONC_503"), _template(503, remaining=None)])
    await db.flush()
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

    again = await CouponService.issue_by_template_id(
        db, 503, 503, quantity=3, client_request_id="BATCH-503"
    )
    assert {int(c.id) for c in again} == {int(c.id) for c in first}
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
async def test_serial_replay_same_key_never_duplicates(mem_db: AsyncSession):
    """串行 30 次同幂等键：始终 1 张、库存只扣 1（模拟重试风暴）。"""
    db = mem_db
    db.add_all([_customer(501, "CU_CONC_501"), _template(501, remaining=10, total_quota=10)])
    await db.flush()
    _patch_sqlite_ids(db, 50_000)
    nos = itertools.count(1)
    with patch(
        "app.services.coupon_service.generate_no",
        side_effect=lambda p: f"{p}S{next(nos):04d}",
    ):
        for _ in range(30):
            issued = await CouponService.issue_by_template_id(
                db, 501, 501, quantity=1, client_request_id="CONC-WELCOME-501"
            )
            assert len(issued) == 1
        await db.commit()

    cnt = int(
        await db.scalar(
            select(func.count())
            .select_from(CustomerCoupon)
            .where(CustomerCoupon.customer_id == 501)
        )
        or 0
    )
    tmpl = await db.get(CouponTemplate, 501)
    assert cnt == 1
    assert int(tmpl.remaining) == 9


@pytest.mark.asyncio
async def test_welcome_serial_idempotent(mem_db: AsyncSession):
    db = mem_db
    db.add_all(
        [
            _customer(504, "CU_CONC_504"),
            _template(504, remaining=20, total_quota=20, name="新客满20减3"),
        ]
    )
    await db.flush()
    _patch_sqlite_ids(db, 80_000)
    nos = itertools.count(1)
    wins = 0
    with patch(
        "app.services.coupon_service.generate_no",
        side_effect=lambda p: f"{p}W{next(nos):04d}",
    ):
        for _ in range(10):
            cc = await CouponService.issue_from_template(db, 504, "新客满20减3")
            if cc is not None:
                wins += 1
        await db.commit()
    assert wins == 1
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


# ---------------------------------------------------------------------------
# MySQL 多会话真实并发（Ubuntu 22.04.5 / CI backend-mysql）
# ---------------------------------------------------------------------------


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_mysql_concurrent_same_idempotency_key_issues_once(mysql_engine):
    """N 会话同时同 client_request_id → 只 1 张，库存扣 1。"""
    Session = async_sessionmaker(mysql_engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        await db.execute(text("DELETE FROM customer_coupons WHERE customer_id=551"))
        await db.execute(text("DELETE FROM coupon_templates WHERE id=551"))
        await db.execute(text("DELETE FROM customers WHERE id=551"))
        db.add_all([_customer(551, "CU_MY_551"), _template(551, remaining=10, total_quota=10)])
        await db.commit()

    n = 16
    barrier = asyncio.Barrier(n)

    async def _one(worker: int) -> list[int]:
        async with Session() as db:
            await barrier.wait()
            try:
                issued = await CouponService.issue_by_template_id(
                    db,
                    551,
                    551,
                    quantity=1,
                    client_request_id="MYSQL-CONC-551",
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
    assert flat
    assert len(set(flat)) == 1

    async with Session() as db:
        cnt = int(
            await db.scalar(
                select(func.count())
                .select_from(CustomerCoupon)
                .where(CustomerCoupon.customer_id == 551)
            )
            or 0
        )
        tmpl = await db.get(CouponTemplate, 551)
        assert cnt == 1
        assert int(tmpl.remaining) == 9


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_mysql_concurrent_quota_never_oversells(mysql_engine):
    """remaining=5，20 路并发各发 1 张 → 恰好 5 张。"""
    Session = async_sessionmaker(mysql_engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        await db.execute(text("DELETE FROM customer_coupons WHERE customer_id=552"))
        await db.execute(text("DELETE FROM coupon_templates WHERE id=552"))
        await db.execute(text("DELETE FROM customers WHERE id=552"))
        db.add_all([_customer(552, "CU_MY_552"), _template(552, remaining=5, total_quota=5)])
        await db.commit()

    n = 20
    barrier = asyncio.Barrier(n)

    async def _one(worker: int) -> int:
        async with Session() as db:
            await barrier.wait()
            try:
                issued = await CouponService.issue_by_template_id(
                    db,
                    552,
                    552,
                    quantity=1,
                    client_request_id=f"MYSQL-QUOTA-552-{worker}",
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
                .where(CustomerCoupon.template_id == 552)
            )
            or 0
        )
        tmpl = await db.get(CouponTemplate, 552)
        assert cnt == 5
        assert int(tmpl.remaining) == 0


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_mysql_welcome_concurrent_no_duplicate(mysql_engine):
    Session = async_sessionmaker(mysql_engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        await db.execute(text("DELETE FROM customer_coupons WHERE customer_id=554"))
        await db.execute(text("DELETE FROM coupon_templates WHERE id=554"))
        await db.execute(text("DELETE FROM customers WHERE id=554"))
        db.add_all(
            [
                _customer(554, "CU_MY_554"),
                _template(554, remaining=20, total_quota=20, name="新客满20减3"),
            ]
        )
        await db.commit()

    barrier = asyncio.Barrier(12)

    async def _one(worker: int) -> int:
        async with Session() as db:
            await barrier.wait()
            cc = await CouponService.issue_from_template(db, 554, "新客满20减3")
            await db.commit()
            return 1 if cc is not None else 0

    wins = await asyncio.gather(*[_one(i) for i in range(12)])
    assert sum(wins) == 1

    async with Session() as db:
        cnt = int(
            await db.scalar(
                select(func.count())
                .select_from(CustomerCoupon)
                .where(CustomerCoupon.customer_id == 554)
            )
            or 0
        )
        tmpl = await db.get(CouponTemplate, 554)
        assert cnt == 1
        assert int(tmpl.remaining) == 19

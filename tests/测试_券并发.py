"""P0-6 优惠券高并发重复发券：原子库存扣减 + client_request_id 幂等。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AppException
from app.services.coupon_service import CouponService


def _mock_tmml(**kw):
    base = {
        "id": 1,
        "name": "测试券",
        "coupon_type": "FIXED",
        "discount_value": 5,
        "min_order_amount": 0,
        "valid_days": 30,
        "status": 1,
        "total_quota": None,
        "remaining": None,
    }
    base.update(kw)
    m = MagicMock()
    for k, v in base.items():
        setattr(m, k, v)
    return m


def _mock_customer():
    c = MagicMock()
    c.id = 10
    c.status = 1
    return c


class _FakeIntegrityError(IntegrityError):
    """模拟 UNIQUE 约束冲突的 IntegrityError。"""

    def __init__(self):
        orig = MagicMock()
        orig.__str__ = lambda self: "UNIQUE constraint failed: coupon_no"
        super().__init__("INSERT ...", {}, orig)


def _mock_begin_nested():
    """模拟 db.begin_nested() async context manager。"""

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    return MagicMock(side_effect=_ctx)


@pytest.mark.asyncio
async def test_issue_quota_insufficient():
    """remaining=2 但 qty=3 → 报库存不足，不写入。"""
    db = MagicMock()
    db.begin_nested = _mock_begin_nested()
    db.get = AsyncMock(side_effect=[_mock_customer(), _mock_tmml(remaining=2, total_quota=10)])
    upd_result = MagicMock()
    upd_result.rowcount = 0  # 库存不足
    db.execute = AsyncMock(return_value=upd_result)
    db.flush = AsyncMock()
    db.rollback = AsyncMock()

    with pytest.raises(AppException) as ei:
        await CouponService.issue_by_template_id(db, 10, 1, quantity=3)
    assert ei.value.code == 40070


@pytest.mark.asyncio
async def test_issue_stale_loaded_remaining_does_not_oversell():
    """内存 tmpl.remaining 是旧快照（10，看似够发 qty=3），但并发已把 DB 实际库存抽空。

    充足性由数据库在原子 UPDATE 执行时按当前行 WHERE remaining >= qty 判定，
    不读取 Python 内存值；DB 不足则 rowcount=0 → 40070，且发生在写券 savepoint 之前，绝不超卖。
    """
    from sqlalchemy.sql import Update

    db = MagicMock()
    db.begin_nested = _mock_begin_nested()
    # 加载到内存的 remaining=10（旧快照），本次请求 qty=3
    db.get = AsyncMock(side_effect=[_mock_customer(), _mock_tmml(remaining=10, total_quota=10)])

    # 第 1 次 execute：client_request_id 幂等回查 → 空
    scalars_empty = MagicMock()
    scalars_empty.all = MagicMock(return_value=[])
    sel_result = MagicMock()
    sel_result.scalars = MagicMock(return_value=scalars_empty)
    # 第 2 次 execute：原子扣减 UPDATE，DB 当前 remaining 已不足 → 0 行
    upd_result = MagicMock()
    upd_result.rowcount = 0
    db.execute = AsyncMock(side_effect=[sel_result, upd_result])
    db.flush = AsyncMock()

    with pytest.raises(AppException) as ei:
        await CouponService.issue_by_template_id(
            db, 10, 1, quantity=3, client_request_id="REQ-CONCURRENT"
        )
    assert ei.value.code == 40070
    # 扣减必须是带守卫的批量 UPDATE（WHERE remaining >= qty 由 DB 判定），而非按内存值 ORM flush
    stmt = db.execute.call_args_list[1].args[0]
    assert isinstance(stmt, Update)
    # 拒绝发生在写券之前：不得进入发券 savepoint
    assert db.begin_nested.call_count == 0


@pytest.mark.asyncio
async def test_issue_quota_ok_atomic_decrement():
    """remaining=5, qty=2 → 原子 UPDATE 成功，写入 2 张。"""
    db = MagicMock()
    db.begin_nested = _mock_begin_nested()
    db.get = AsyncMock(side_effect=[_mock_customer(), _mock_tmml(remaining=5, total_quota=10)])

    # SELECT existing by client_request_id → 空列表
    scalars_empty = MagicMock()
    scalars_empty.all = MagicMock(return_value=[])
    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=scalars_empty)
    # UPDATE result
    upd_result = MagicMock()
    upd_result.rowcount = 1
    db.execute = AsyncMock(side_effect=[exec_result, upd_result])
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    issued = await CouponService.issue_by_template_id(
        db, 10, 1, quantity=2, client_request_id="REQ-001"
    )
    assert len(issued) == 2
    assert all(cc.client_request_id == "REQ-001" for cc in issued)


@pytest.mark.asyncio
async def test_issue_idempotent_returns_existing():
    """同 client_request_id 重复请求 → 返回已有行，不重复扣库存。"""
    existing_cc = MagicMock(id=99, client_request_id="REQ-002")
    db = MagicMock()
    db.begin_nested = _mock_begin_nested()
    db.get = AsyncMock(side_effect=[_mock_customer(), _mock_tmml(remaining=5)])
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=[existing_cc])
    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=scalars)
    db.execute = AsyncMock(return_value=exec_result)

    issued = await CouponService.issue_by_template_id(
        db, 10, 1, quantity=1, client_request_id="REQ-002"
    )
    assert len(issued) == 1
    assert issued[0].id == 99
    # 只执行了 1 次 SELECT（幂等回查），不应有 UPDATE 库存
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_issue_unlimited_quota_skips_decrement():
    """remaining=None → 不限量，跳过库存扣减。"""
    db = MagicMock()
    db.begin_nested = _mock_begin_nested()
    db.get = AsyncMock(side_effect=[_mock_customer(), _mock_tmml(remaining=None, total_quota=None)])
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    issued = await CouponService.issue_by_template_id(db, 10, 1, quantity=1)
    assert len(issued) == 1
    # 不限量且无 client_request_id → 不执行任何 SELECT/UPDATE
    assert db.execute.await_count == 0


@pytest.mark.asyncio
async def test_issue_integrity_error_refunds_quota():
    """coupon_no 冲突时回补库存。"""
    db = MagicMock()
    db.begin_nested = _mock_begin_nested()
    db.get = AsyncMock(side_effect=[_mock_customer(), _mock_tmml(remaining=5, total_quota=10)])
    # SELECT existing → 空
    scalars_empty = MagicMock()
    scalars_empty.all = MagicMock(return_value=[])
    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=scalars_empty)
    # UPDATE 库存成功
    upd_result = MagicMock()
    upd_result.rowcount = 1
    # rollback 后回补 UPDATE
    refund_result = MagicMock()
    db.execute = AsyncMock(side_effect=[exec_result, upd_result, refund_result])
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    # 模拟 flush 抛 IntegrityError（UNIQUE 约束）
    async def _raise():
        raise _FakeIntegrityError()
    db.flush = AsyncMock(side_effect=_raise)

    with pytest.raises(AppException) as ei:
        await CouponService.issue_by_template_id(db, 10, 1, quantity=2)
    assert ei.value.code == 50020
    # 验证回补库存的 UPDATE 被执行（扣减 + 回补 = 2 次 execute，无 client_request_id 故无 SELECT）
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_create_template_initializes_remaining():
    """创建模板时 remaining = total_quota。"""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    tmpl = await CouponService.create_template(
        db,
        {"name": "限量券", "discount_value": 5, "total_quota": 100},
    )
    assert tmpl.total_quota == 100
    assert tmpl.remaining == 100


@pytest.mark.asyncio
async def test_create_template_no_quota():
    """total_quota=None → remaining=None。"""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    tmpl = await CouponService.create_template(
        db,
        {"name": "不限量券", "discount_value": 5},
    )
    assert tmpl.total_quota is None
    assert tmpl.remaining is None


@pytest.mark.asyncio
async def test_create_template_quota_zero_disables_issuance():
    """total_quota=0 → remaining=0，禁止发放（而非静默变成 None=不限量）。"""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    tmpl = await CouponService.create_template(
        db,
        {"name": "停发券", "discount_value": 5, "total_quota": 0},
    )
    assert tmpl.total_quota == 0
    assert tmpl.remaining == 0

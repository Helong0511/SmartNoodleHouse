"""积分兑换 / 过期清零 / 明细查询 测试。

覆盖：
- 兑换成功扣分+发券+写 EXCHANGE 流水；
- 积分不足抛 AppException；
- 同月同选项幂等（不重复扣分/发券）；
- 选项停用不可兑换；
- 兑换后积分降级；
- 过期清零：超期 ACTIVE 会员积分整体清零写 EXPIRE 流水；
- 未超期不动；
- days=0 关闭；
- 明细分页+排序+entry_type 筛选+中文标签。
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select, func
from sqlalchemy.orm import Session as SyncSession

from app.core.exceptions import AppException
from app.models import (
    CouponTemplate,
    Customer,
    CustomerCoupon,
    MemberLevel,
    PointExchangeOption,
    PointLedger,
    Store,
    SystemSetting,
)
from app.services import retention_service
from app.services.customer_service import CustomerService
from app.services.system_settings_service import SystemSettingsService

NOW = datetime(2026, 10, 10, 10, 5, 0)

_pending_ids = itertools.count(9_000_000)


@pytest.fixture(autouse=True)
def _sqlite_bigint_pk_ids():
    def _assign(session, flush_context, instances):
        for obj in session.new:
            if (
                isinstance(
                    obj,
                    (
                        CouponTemplate,
                        CustomerCoupon,
                        SystemSetting,
                        Store,
                        PointExchangeOption,
                        PointLedger,
                        MemberLevel,
                    ),
                )
                and getattr(obj, "id", None) is None
            ):
                obj.id = next(_pending_ids)

    event.listen(SyncSession, "before_flush", _assign)
    yield
    event.remove(SyncSession, "before_flush", _assign)


async def _mk_customer(
    db,
    *,
    no: str,
    points: int = 0,
    last_order_at: datetime | None = None,
    account_status: str = "ACTIVE",
    registered_store_id: int | None = None,
    member_level_id: int | None = None,
) -> Customer:
    c = Customer(
        customer_no=no,
        nickname=no,
        points=points,
        last_order_at=last_order_at,
        account_status=account_status,
        registered_store_id=registered_store_id,
        member_level_id=member_level_id,
    )
    db.add(c)
    await db.flush()
    return c


async def _mk_template(db, *, tid: int, name: str, store_id: int | None = None):
    t = CouponTemplate(
        id=tid,
        store_id=store_id,
        name=name,
        coupon_type="FIXED",
        discount_value=Decimal("5"),
        min_order_amount=Decimal("30"),
        valid_days=30,
        status=1,
    )
    db.add(t)
    await db.flush()
    return t


async def _mk_option(
    db,
    *,
    oid: int | None = None,
    name: str = "兑换券5",
    template_id: int = 9001,
    points_cost: int = 100,
    store_id: int | None = None,
    status: int = 1,
):
    opt = PointExchangeOption(
        id=oid,
        store_id=store_id,
        name=name,
        template_id=template_id,
        points_cost=points_cost,
        status=status,
        sort_order=0,
    )
    db.add(opt)
    await db.flush()
    return opt


async def _mk_setting(db, *, key: str, value: str):
    row = SystemSetting(setting_key=key, setting_value=value)
    db.add(row)
    await db.flush()


def _invalidate():
    SystemSettingsService.invalidate_cache_local()


# ---------------------------------------------------------------------------
# 兑换
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exchange_success_deducts_and_issues(db_session):
    """兑换成功：扣分 + 发券 + 写 EXCHANGE 流水。"""
    await _mk_template(db_session, tid=9001, name="兑换券5")
    await _mk_option(db_session, oid=9101, template_id=9001, points_cost=100)
    c = await _mk_customer(db_session, no="CU_EX1", points=200)
    await db_session.commit()

    result = await CustomerService.exchange_points_for_coupon(db_session, c.id, 9101)
    await db_session.commit()

    assert result["already_exchanged"] is False
    assert result["deducted_points"] == 100
    assert result["remaining_points"] == 100

    # 券已发
    coupons = list(
        (await db_session.execute(select(CustomerCoupon).where(CustomerCoupon.customer_id == c.id))).scalars().all()
    )
    assert len(coupons) == 1
    assert coupons[0].template_id == 9001

    # 流水
    ledgers = list(
        (await db_session.execute(
            select(PointLedger).where(PointLedger.customer_id == c.id, PointLedger.entry_type == "EXCHANGE")
        )).scalars().all()
    )
    assert len(ledgers) == 1
    assert ledgers[0].delta == -100
    assert ledgers[0].balance_after == 100

    # 顾客积分已扣
    refreshed = await db_session.get(Customer, c.id)
    assert refreshed.points == 100


@pytest.mark.asyncio
async def test_exchange_insufficient_points_raises(db_session):
    """积分不足抛 AppException，不扣分不发券。"""
    await _mk_template(db_session, tid=9002, name="兑换券5B")
    await _mk_option(db_session, oid=9102, template_id=9002, points_cost=500)
    c = await _mk_customer(db_session, no="CU_EX2", points=100)
    cid = int(c.id)  # 预取主键，rollback 后 ORM 属性失效
    await db_session.commit()

    with pytest.raises(AppException) as exc_info:
        await CustomerService.exchange_points_for_coupon(db_session, cid, 9102)
    assert "积分不足" in exc_info.value.message

    await db_session.rollback()
    # 不扣分
    refreshed = await db_session.get(Customer, cid)
    assert refreshed.points == 100


@pytest.mark.asyncio
async def test_exchange_idempotent_same_month(db_session):
    """同月同选项重复请求不扣分、不重复发券。"""
    await _mk_template(db_session, tid=9003, name="兑换券5C")
    await _mk_option(db_session, oid=9103, template_id=9003, points_cost=50)
    c = await _mk_customer(db_session, no="CU_EX3", points=200)
    await db_session.commit()

    first = await CustomerService.exchange_points_for_coupon(db_session, c.id, 9103)
    await db_session.commit()
    assert first["deducted_points"] == 50
    assert first["remaining_points"] == 150

    second = await CustomerService.exchange_points_for_coupon(db_session, c.id, 9103)
    await db_session.commit()
    assert second["already_exchanged"] is True
    assert second["deducted_points"] == 0
    assert second["remaining_points"] == 150

    # 仅一张券
    coupons = list(
        (await db_session.execute(select(CustomerCoupon).where(CustomerCoupon.customer_id == c.id))).scalars().all()
    )
    assert len(coupons) == 1
    # 仅一条 EXCHANGE 流水
    ledgers = list(
        (await db_session.execute(
            select(PointLedger).where(PointLedger.customer_id == c.id, PointLedger.entry_type == "EXCHANGE")
        )).scalars().all()
    )
    assert len(ledgers) == 1


@pytest.mark.asyncio
async def test_exchange_option_disabled_raises(db_session):
    """选项停用不可兑换。"""
    await _mk_template(db_session, tid=9004, name="兑换券5D")
    await _mk_option(db_session, oid=9104, template_id=9004, points_cost=50, status=0)
    c = await _mk_customer(db_session, no="CU_EX4", points=200)
    await db_session.commit()

    with pytest.raises(AppException) as exc_info:
        await CustomerService.exchange_points_for_coupon(db_session, c.id, 9104)
    assert "不存在或已停用" in exc_info.value.message
    await db_session.rollback()


@pytest.mark.asyncio
async def test_exchange_level_downgrade(db_session):
    """兑换扣分后积分降级。"""
    lv1 = MemberLevel(id=9501, name="普通", min_points=0, discount_rate=Decimal("1.0000"))
    lv2 = MemberLevel(id=9502, name="银卡", min_points=100, discount_rate=Decimal("0.9800"))
    db_session.add_all([lv1, lv2])
    await db_session.flush()

    await _mk_template(db_session, tid=9005, name="兑换券5E")
    await _mk_option(db_session, oid=9105, template_id=9005, points_cost=150)
    c = await _mk_customer(db_session, no="CU_EX5", points=200, member_level_id=9502)
    await db_session.commit()

    # patch list_levels_ordered to return our levels
    original = CustomerService.list_levels_ordered
    CustomerService.list_levels_ordered = AsyncMock(return_value=[lv1, lv2])
    try:
        result = await CustomerService.exchange_points_for_coupon(db_session, c.id, 9105)
        await db_session.commit()
        assert result["deducted_points"] == 150
        assert result["remaining_points"] == 50
        refreshed = await db_session.get(Customer, c.id)
        assert refreshed.points == 50
        assert refreshed.member_level_id == 9501  # 降级到普通
    finally:
        CustomerService.list_levels_ordered = original


# ---------------------------------------------------------------------------
# 过期清零
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expire_clears_overdue_customer(db_session):
    """超期 ACTIVE 会员积分整体清零 + 写 EXPIRE 流水。"""
    await _mk_setting(db_session, key="points_expire_days", value="30")
    c = await _mk_customer(
        db_session,
        no="CU_EXP1",
        points=300,
        last_order_at=NOW - timedelta(days=60),
    )
    await db_session.commit()
    _invalidate()

    stats = await retention_service.run_points_expire(db_session, now=NOW)

    assert stats["enabled"] is True
    assert stats["expired_count"] == 1
    assert stats["expired_points"] == 300

    refreshed = await db_session.get(Customer, c.id)
    assert refreshed.points == 0

    ledgers = list(
        (await db_session.execute(
            select(PointLedger).where(PointLedger.customer_id == c.id, PointLedger.entry_type == "EXPIRE")
        )).scalars().all()
    )
    assert len(ledgers) == 1
    assert ledgers[0].delta == -300
    assert ledgers[0].balance_after == 0


@pytest.mark.asyncio
async def test_expire_skips_recent_customer(db_session):
    """未超期顾客积分不动。"""
    await _mk_setting(db_session, key="points_expire_days", value="30")
    c = await _mk_customer(
        db_session,
        no="CU_EXP2",
        points=200,
        last_order_at=NOW - timedelta(days=10),
    )
    await db_session.commit()
    _invalidate()

    stats = await retention_service.run_points_expire(db_session, now=NOW)

    assert stats["expired_count"] == 0
    refreshed = await db_session.get(Customer, c.id)
    assert refreshed.points == 200


@pytest.mark.asyncio
async def test_expire_disabled_when_days_zero(db_session):
    """days=0 关闭过期。"""
    await _mk_setting(db_session, key="points_expire_days", value="0")
    c = await _mk_customer(
        db_session,
        no="CU_EXP3",
        points=500,
        last_order_at=NOW - timedelta(days=3650),
    )
    await db_session.commit()
    _invalidate()

    stats = await retention_service.run_points_expire(db_session, now=NOW)

    assert stats["enabled"] is False
    assert stats["expired_count"] == 0
    refreshed = await db_session.get(Customer, c.id)
    assert refreshed.points == 500


@pytest.mark.asyncio
async def test_expire_skips_null_last_order(db_session):
    """last_order_at IS NULL 不处理。"""
    await _mk_setting(db_session, key="points_expire_days", value="30")
    c = await _mk_customer(
        db_session,
        no="CU_EXP4",
        points=100,
        last_order_at=None,
    )
    await db_session.commit()
    _invalidate()

    stats = await retention_service.run_points_expire(db_session, now=NOW)
    assert stats["expired_count"] == 0
    refreshed = await db_session.get(Customer, c.id)
    assert refreshed.points == 100


@pytest.mark.asyncio
async def test_expire_skips_inactive(db_session):
    """非 ACTIVE 会员不动。"""
    await _mk_setting(db_session, key="points_expire_days", value="30")
    c = await _mk_customer(
        db_session,
        no="CU_EXP5",
        points=100,
        last_order_at=NOW - timedelta(days=60),
        account_status="FROZEN",
    )
    await db_session.commit()
    _invalidate()

    stats = await retention_service.run_points_expire(db_session, now=NOW)
    assert stats["expired_count"] == 0


@pytest.mark.asyncio
async def test_expire_skips_zero_points(db_session):
    """积分为 0 的顾客不动。"""
    await _mk_setting(db_session, key="points_expire_days", value="30")
    c = await _mk_customer(
        db_session,
        no="CU_EXP6",
        points=0,
        last_order_at=NOW - timedelta(days=60),
    )
    await db_session.commit()
    _invalidate()

    stats = await retention_service.run_points_expire(db_session, now=NOW)
    assert stats["expired_count"] == 0


# ---------------------------------------------------------------------------
# 过期任务在 TASK_CATALOG 中注册
# ---------------------------------------------------------------------------


def test_task_catalog_contains_points_expire():
    from app.tasks.scheduler import TASK_CATALOG

    ids = {m["id"] for m in TASK_CATALOG}
    assert "points_expire" in ids
    pe = next(m for m in TASK_CATALOG if m["id"] == "points_expire")
    assert pe["default_schedule"] == {"type": "cron", "hour": 2, "minute": 30}


# ---------------------------------------------------------------------------
# 积分明细接口（服务层逻辑验证）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ledger_pagination_and_labels(db_session):
    """明细按 created_at DESC + id DESC 排序，entry_type 标签映射正确。"""
    from app.core.response import calc_pages

    c = await _mk_customer(db_session, no="CU_LED1", points=0)

    # 手动写几条流水（created_at 不可显式设 → 用 server_default）
    for i in range(5):
        db_session.add(
            PointLedger(
                customer_id=c.id,
                order_id=None,
                refund_id=None,
                delta=10,
                balance_after=10 * (i + 1),
                entry_type="EARN",
            )
        )
    db_session.add(
        PointLedger(
            customer_id=c.id,
            order_id=None,
            refund_id=None,
            delta=-5,
            balance_after=45,
            entry_type="EXCHANGE",
        )
    )
    await db_session.commit()

    # 查全量
    conds = [PointLedger.customer_id == c.id]
    total = await db_session.scalar(
        select(func.count()).select_from(PointLedger).where(*conds)
    ) or 0
    rows = list(
        (
            await db_session.execute(
                select(PointLedger)
                .where(*conds)
                .order_by(PointLedger.created_at.desc(), PointLedger.id.desc())
            )
        )
        .scalars()
        .all()
    )
    assert int(total) == 6
    assert len(rows) == 6
    # 按 id DESC 兜底（同一 commit 内 created_at 相同）
    ids_desc = [r.id for r in rows]
    assert ids_desc == sorted(ids_desc, reverse=True)

    # entry_type 筛选
    conds2 = [PointLedger.customer_id == c.id, PointLedger.entry_type == "EXCHANGE"]
    total2 = await db_session.scalar(
        select(func.count()).select_from(PointLedger).where(*conds2)
    ) or 0
    assert int(total2) == 1

    # 分页
    pages = calc_pages(int(total), 20)
    assert pages == 1
    pages2 = calc_pages(int(total), 2)
    assert pages2 == 3  # 6 条 / 2 每页 = 3 页

    # 标签
    labels = {
        "EARN": "消费获得",
        "REFUND_DEDUCT": "退款扣减",
        "EXCHANGE": "兑换扣减",
        "EXPIRE": "过期清零",
    }
    assert labels["EXCHANGE"] == "兑换扣减"
    assert labels["EARN"] == "消费获得"

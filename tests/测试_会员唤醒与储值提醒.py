"""会员沉睡唤醒发券 + 储值余额偏低提醒（营销定时任务服务层测试）。

直接调用 retention_service 的服务函数验证：
- 30/90 天档命中与不命中、档位互斥；
- last_order_at NULL / 非活跃 / 模板名空 不发；
- 发券幂等（sleep_wake:{30|90}:{cid}:{tid}，与 WELCOME 键隔离）；
- 余额阈值边界（=阈值、>阈值、=0 不提醒）；
- Redis 防重键 SET NX EX 行为（FakeRedis 模拟）与 Redis 不可用 fail-open。
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session as SyncSession

from app.models import CouponTemplate, Customer, CustomerCoupon, Store, SystemSetting
from app.services import retention_service
from app.services.system_settings_service import SystemSettingsService

# 固定业务时间（仅用于 last_order_at 相对计算）
NOW = datetime(2026, 10, 10, 10, 5, 0)

# SQLite 对 BigInteger PK 无自增（非 INTEGER PRIMARY KEY 不作 rowid 别名），
# 与 tests/测试_券批量发放.py 同思路：flush 前为发券链路涉及表补显式 id。
# 起点取 8_000_000 避开其他测试手写的显式 id。
_pending_ids = itertools.count(8_000_000)


@pytest.fixture(autouse=True)
def _sqlite_bigint_pk_ids():
    """为 BigInteger 主键模型在 SQLite 上补自增 id（仅本文件测试期间生效）。"""

    def _assign(session, flush_context, instances):
        for obj in session.new:
            if (
                isinstance(obj, (CouponTemplate, CustomerCoupon, SystemSetting, Store))
                and getattr(obj, "id", None) is None
            ):
                obj.id = next(_pending_ids)

    event.listen(SyncSession, "before_flush", _assign)
    yield
    event.remove(SyncSession, "before_flush", _assign)


class FakeRedis:
    """模拟 SET NX EX 语义，验证余额提醒 30 天防重键行为。"""

    def __init__(self):
        self.kv: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return False
        self.kv[key] = str(value)
        if ex is not None:
            self.ttl[key] = int(ex)
        return True


async def _mk_customer(
    db,
    *,
    no: str,
    last_order_at: datetime | None = None,
    account_status: str = "ACTIVE",
    principal: str = "0.00",
    gift: str = "0.00",
    registered_store_id: int | None = None,
) -> Customer:
    c = Customer(
        customer_no=no,
        nickname=no,
        last_order_at=last_order_at,
        account_status=account_status,
        balance_principal=Decimal(principal),
        balance_gift=Decimal(gift),
        registered_store_id=registered_store_id,
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


async def _mk_setting(db, *, key: str, value: str):
    row = SystemSetting(setting_key=key, setting_value=value)
    db.add(row)
    await db.flush()


async def _count_coupons(db) -> list[CustomerCoupon]:
    return list((await db.execute(select(CustomerCoupon))).scalars().all())


def _invalidate_settings_cache():
    SystemSettingsService.invalidate_cache_local()


# ---------------------------------------------------------------------------
# 任务注册元数据
# ---------------------------------------------------------------------------


def test_task_catalog_contains_marketing_jobs():
    from app.tasks.scheduler import TASK_CATALOG

    ids = {m["id"] for m in TASK_CATALOG}
    assert {"sleep_member_wake", "balance_low_remind"} <= ids
    wake = next(m for m in TASK_CATALOG if m["id"] == "sleep_member_wake")
    assert wake["default_schedule"] == {"type": "cron", "hour": 10, "minute": 5}
    bal = next(m for m in TASK_CATALOG if m["id"] == "balance_low_remind")
    assert bal["default_schedule"] == {"type": "cron", "hour": 11, "minute": 5}


# ---------------------------------------------------------------------------
# 沉睡会员唤醒
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sleep_wake_30d_90d_hits_and_tiers_disjoint(db_session):
    await _mk_template(db_session, tid=8001, name="唤醒券30")
    await _mk_template(db_session, tid=8002, name="唤醒券90")
    await _mk_setting(db_session, key="sleep_wake_coupon_name_30", value="唤醒券30")
    await _mk_setting(db_session, key="sleep_wake_coupon_name_90", value="唤醒券90")
    c30 = await _mk_customer(db_session, no="CU_WAKE30", last_order_at=NOW - timedelta(days=40))
    c90 = await _mk_customer(db_session, no="CU_WAKE90", last_order_at=NOW - timedelta(days=100))
    await db_session.commit()
    _invalidate_settings_cache()

    stats = await retention_service.run_sleep_wake(db_session, now=NOW)

    assert stats["enabled_30"] is True
    assert stats["enabled_90"] is True
    assert stats["issued_30"] == 1
    assert stats["issued_90"] == 1
    coupons = await _count_coupons(db_session)
    # 30 档顾客不落 90 档、90 档顾客不落 30 档（档位区间互斥）
    assert {(cc.customer_id, cc.template_id) for cc in coupons} == {
        (c30.id, 8001),
        (c90.id, 8002),
    }


@pytest.mark.asyncio
async def test_sleep_wake_skips_recent_order(db_session):
    """30 天内下过单的顾客不属于沉睡，不发券。"""
    await _mk_template(db_session, tid=8011, name="唤醒券30")
    await _mk_setting(db_session, key="sleep_wake_coupon_name_30", value="唤醒券30")
    await _mk_customer(db_session, no="CU_RECENT", last_order_at=NOW - timedelta(days=10))
    await db_session.commit()
    _invalidate_settings_cache()

    stats = await retention_service.run_sleep_wake(db_session, now=NOW)

    assert stats["issued_30"] == 0
    assert await _count_coupons(db_session) == []


@pytest.mark.asyncio
async def test_sleep_wake_skips_null_last_order(db_session):
    """last_order_at IS NULL 的注册顾客无消费记录，不发券。"""
    await _mk_template(db_session, tid=8012, name="唤醒券30")
    await _mk_setting(db_session, key="sleep_wake_coupon_name_30", value="唤醒券30")
    await _mk_customer(db_session, no="CU_NOORDER", last_order_at=None)
    await db_session.commit()
    _invalidate_settings_cache()

    stats = await retention_service.run_sleep_wake(db_session, now=NOW)

    assert stats["issued_30"] == 0
    assert stats["issued_90"] == 0
    assert await _count_coupons(db_session) == []


@pytest.mark.asyncio
async def test_sleep_wake_skips_inactive_customer(db_session):
    await _mk_template(db_session, tid=8013, name="唤醒券90")
    await _mk_setting(db_session, key="sleep_wake_coupon_name_90", value="唤醒券90")
    await _mk_customer(
        db_session,
        no="CU_FROZEN",
        last_order_at=NOW - timedelta(days=120),
        account_status="FROZEN",
    )
    await db_session.commit()
    _invalidate_settings_cache()

    stats = await retention_service.run_sleep_wake(db_session, now=NOW)

    assert stats["issued_90"] == 0
    assert await _count_coupons(db_session) == []


@pytest.mark.asyncio
async def test_sleep_wake_disabled_when_names_missing_or_empty(db_session):
    """模板名缺失或空串 = 该档关闭。"""
    await _mk_customer(db_session, no="CU_ANY", last_order_at=NOW - timedelta(days=60))
    await db_session.commit()

    stats = await retention_service.run_sleep_wake(db_session, now=NOW)
    assert stats["enabled_30"] is False
    assert stats["enabled_90"] is False
    assert await _count_coupons(db_session) == []

    # 显式空串同样关闭
    await _mk_setting(db_session, key="sleep_wake_coupon_name_30", value="")
    await db_session.commit()
    _invalidate_settings_cache()
    stats2 = await retention_service.run_sleep_wake(db_session, now=NOW)
    assert stats2["enabled_30"] is False
    assert await _count_coupons(db_session) == []


@pytest.mark.asyncio
async def test_sleep_wake_idempotent_on_second_run(db_session):
    """每顾客每档仅发一次（sleep_wake:30:{cid}:{tid}），重跑不重复。"""
    from app.services.coupon_service import CouponService

    await _mk_template(db_session, tid=8014, name="唤醒券30")
    await _mk_setting(db_session, key="sleep_wake_coupon_name_30", value="唤醒券30")
    c = await _mk_customer(db_session, no="CU_IDEM", last_order_at=NOW - timedelta(days=45))
    await db_session.commit()
    _invalidate_settings_cache()

    first = await retention_service.run_sleep_wake(db_session, now=NOW)
    assert first["issued_30"] == 1
    coupons = await _count_coupons(db_session)
    assert len(coupons) == 1
    assert coupons[0].client_request_id == CouponService.sleep_wake_client_request_id(
        "30", int(c.id), 8014
    )
    assert not str(coupons[0].client_request_id).startswith("WELCOME:")

    second = await retention_service.run_sleep_wake(db_session, now=NOW)
    assert second["issued_30"] == 0
    assert len(await _count_coupons(db_session)) == 1


@pytest.mark.asyncio
async def test_sleep_wake_prefers_registered_store_template(db_session):
    """store_id 传顾客 registered_store_id：同名模板时本店优先于总部。"""
    store = Store(store_no="ST_WAKE", name="唤醒测试店")
    db_session.add(store)
    await db_session.flush()
    await _mk_template(db_session, tid=8015, name="唤醒券S", store_id=store.id)
    await _mk_template(db_session, tid=8016, name="唤醒券S", store_id=None)
    await _mk_setting(db_session, key="sleep_wake_coupon_name_30", value="唤醒券S")
    c = await _mk_customer(
        db_session,
        no="CU_STORE",
        last_order_at=NOW - timedelta(days=50),
        registered_store_id=store.id,
    )
    await db_session.commit()
    _invalidate_settings_cache()

    stats = await retention_service.run_sleep_wake(db_session, now=NOW)

    assert stats["issued_30"] == 1
    coupons = await _count_coupons(db_session)
    assert len(coupons) == 1
    assert coupons[0].customer_id == c.id
    assert coupons[0].template_id == 8015  # 本店模板，而非总部 8016


@pytest.mark.asyncio
async def test_sleep_wake_batches_by_id(db_session):
    """batch_size=1 时按 id 分批扫描，不漏发（keyset 分页正确）。"""
    await _mk_template(db_session, tid=8017, name="唤醒券90")
    await _mk_setting(db_session, key="sleep_wake_coupon_name_90", value="唤醒券90")
    await _mk_customer(db_session, no="CU_B1", last_order_at=NOW - timedelta(days=95))
    await _mk_customer(db_session, no="CU_B2", last_order_at=NOW - timedelta(days=100))
    await _mk_customer(db_session, no="CU_B3", last_order_at=NOW - timedelta(days=110))
    await db_session.commit()
    _invalidate_settings_cache()

    stats = await retention_service.run_sleep_wake(db_session, now=NOW, batch_size=1)

    assert stats["issued_90"] == 3
    assert len(await _count_coupons(db_session)) == 3


# ---------------------------------------------------------------------------
# 储值余额提醒
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_balance_remind_hits_low_balance(db_session):
    await _mk_setting(db_session, key="balance_low_threshold", value="50")
    c = await _mk_customer(db_session, no="CU_BAL1", principal="30.00", gift="19.99")
    await db_session.commit()
    _invalidate_settings_cache()

    fake = FakeRedis()
    with patch.object(retention_service, "try_get_redis", AsyncMock(return_value=fake)):
        stats = await retention_service.run_balance_low_remind(db_session)

    assert stats["enabled"] is True
    assert stats["reminded"] == 1
    assert stats["customer_ids"] == [c.id]
    key = f"snh:notify:balance_low:{c.id}"
    assert fake.kv[key] == "1"
    assert fake.ttl[key] == 30 * 24 * 3600  # 30 天


@pytest.mark.asyncio
async def test_balance_remind_threshold_boundaries(db_session):
    """余额=阈值、>阈值、=0 都不提醒；仅 0<余额<阈值 提醒。"""
    await _mk_setting(db_session, key="balance_low_threshold", value="50")
    c_eq = await _mk_customer(db_session, no="CU_EQ", principal="20.00", gift="30.00")
    c_gt = await _mk_customer(db_session, no="CU_GT", principal="50.00", gift="1.00")
    await _mk_customer(db_session, no="CU_ZERO", principal="0.00", gift="0.00")
    c_low = await _mk_customer(db_session, no="CU_LOW", principal="10.00", gift="10.00")
    await db_session.commit()
    _invalidate_settings_cache()

    fake = FakeRedis()
    with patch.object(retention_service, "try_get_redis", AsyncMock(return_value=fake)):
        stats = await retention_service.run_balance_low_remind(db_session)

    assert stats["customer_ids"] == [c_low.id]
    assert c_eq.id not in stats["customer_ids"]
    assert c_gt.id not in stats["customer_ids"]


@pytest.mark.asyncio
async def test_balance_remind_dedup_within_30d(db_session):
    """Redis SET NX 防重：30 天内重复运行只提醒一次。"""
    await _mk_setting(db_session, key="balance_low_threshold", value="100")
    await _mk_customer(db_session, no="CU_DEDUP", principal="5.00", gift="5.00")
    await db_session.commit()
    _invalidate_settings_cache()

    fake = FakeRedis()
    with patch.object(retention_service, "try_get_redis", AsyncMock(return_value=fake)):
        first = await retention_service.run_balance_low_remind(db_session)
    assert first["reminded"] == 1

    with patch.object(retention_service, "try_get_redis", AsyncMock(return_value=fake)):
        second = await retention_service.run_balance_low_remind(db_session)
    assert second["reminded"] == 0  # 键已存在，NX 失败 → 不再提醒
    assert len(fake.kv) == 1


@pytest.mark.asyncio
async def test_balance_remind_disabled_without_threshold(db_session):
    """阈值缺失/0/非法 = 关闭，且完全不触达 Redis。"""
    await _mk_customer(db_session, no="CU_OFF", principal="1.00", gift="1.00")
    await db_session.commit()

    never = AsyncMock(side_effect=AssertionError("关闭时不应触达 Redis"))
    with patch.object(retention_service, "try_get_redis", never):
        missing = await retention_service.run_balance_low_remind(db_session)
        assert missing["enabled"] is False
        assert missing["reminded"] == 0

        await _mk_setting(db_session, key="balance_low_threshold", value="0")
        await db_session.commit()
        _invalidate_settings_cache()
        zero = await retention_service.run_balance_low_remind(db_session)
        assert zero["enabled"] is False

        row = (await db_session.execute(select(SystemSetting))).scalars().one()
        row.setting_value = "abc"  # 非法 decimal
        await db_session.commit()
        _invalidate_settings_cache()
        invalid = await retention_service.run_balance_low_remind(db_session)
        assert invalid["enabled"] is False

    assert await _count_coupons(db_session) == []


@pytest.mark.asyncio
async def test_balance_remind_redis_unavailable_fail_open(db_session):
    """Redis 不可用时 fail-open：跳过本轮、不抛异常、不阻塞。"""
    await _mk_setting(db_session, key="balance_low_threshold", value="50")
    await _mk_customer(db_session, no="CU_NOREDIS", principal="3.00", gift="3.00")
    await db_session.commit()
    _invalidate_settings_cache()

    with patch.object(retention_service, "try_get_redis", AsyncMock(return_value=None)):
        stats = await retention_service.run_balance_low_remind(db_session)

    assert stats["skipped"] == "redis_unavailable"
    assert stats["reminded"] == 0
    assert stats["customer_ids"] == []


@pytest.mark.asyncio
async def test_balance_remind_skips_inactive_customer(db_session):
    await _mk_setting(db_session, key="balance_low_threshold", value="50")
    await _mk_customer(
        db_session,
        no="CU_BAL_FROZEN",
        principal="1.00",
        gift="1.00",
        account_status="FROZEN",
    )
    await db_session.commit()
    _invalidate_settings_cache()

    fake = FakeRedis()
    with patch.object(retention_service, "try_get_redis", AsyncMock(return_value=fake)):
        stats = await retention_service.run_balance_low_remind(db_session)

    assert stats["reminded"] == 0
    assert fake.kv == {}

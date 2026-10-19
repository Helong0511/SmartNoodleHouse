"""日常体验功能：顾客催单 / 支付成功领券 / 未制作整单自助退款 / 预约取餐 / 生日双倍积分。"""
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.security import create_customer_token
from app.main import app
from app.models import (
    CouponTemplate,
    Customer,
    CustomerCoupon,
    Payment,
    SystemSetting,
)
from app.services.customer_service import CustomerService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 种子工具
# ---------------------------------------------------------------------------

import itertools

_seq = itertools.count(1)


async def _seed_store(db):
    from app.models import Store

    store = Store(
        id=next(_seq) + 900,
        name="测试门店",
        store_no=f"S{next(_seq):04d}",
        status=1,
    )
    db.add(store)
    await db.flush()
    return store


async def _seed_customer(db, birthday=None):
    from app.core.biz_no import BIZ_CU, generate_no

    cust = Customer(
        id=next(_seq) + 500,
        customer_no=generate_no(BIZ_CU),
        nickname="测试顾客",
        status=1,
        account_status="ACTIVE",
        member_status="MEMBER",
        birthday=birthday,
    )
    db.add(cust)
    await db.flush()
    return cust


async def _seed_order(db, store, customer, *, status="CONFIRMED", payment_status="PAID", channel="H5"):
    from app.models import Order

    order = Order(
        id=next(_seq) + 3000,
        order_no=f"T{next(_seq)}{datetime.now().strftime('%H%M%S')}",
        customer_id=customer.id,
        store_id=store.id,
        status=status,
        payment_status=payment_status,
        channel=channel,
        dining_type="TAKEAWAY",
        subtotal=Decimal("30.00"),
        total_amount=Decimal("30.00"),
        ordered_at=datetime.now(),
        paid_at=datetime.now() if payment_status == "PAID" else None,
    )
    db.add(order)
    await db.flush()
    return order


async def _seed_payment(db, order, customer, *, amount=Decimal("30.00")):
    payment = Payment(
        id=next(_seq) + 7000,
        payment_no=f"P{next(_seq)}{datetime.now().strftime('%H%M%S')}",
        order_id=order.id,
        customer_id=customer.id,
        provider="WECHAT",
        channel="mini",
        amount=amount,
        status="PAID",
        paid_at=datetime.now(),
    )
    db.add(payment)
    await db.flush()
    return payment


async def _seed_coupon_template(db, name="支付成功券"):
    tmpl = CouponTemplate(
        id=next(_seq) + 100,
        name=name,
        coupon_type="FIXED",
        discount_value=Decimal("5.00"),
        min_order_amount=Decimal("0.00"),
        valid_days=30,
        status=1,
    )
    db.add(tmpl)
    await db.flush()
    return tmpl


async def _enable_setting(db, key, value):
    from sqlalchemy import func, select

    cur = await db.scalar(select(func.coalesce(func.max(SystemSetting.id), 0)))
    sid = int(cur or 0) + 1
    db.add(SystemSetting(id=sid, setting_key=key, setting_value=value))
    await db.flush()
    from app.services.system_settings_service import SystemSettingsService

    SystemSettingsService.invalidate_cache_local()


@pytest_asyncio.fixture
async def api(db_session, client):
    """依赖覆盖：get_db → db_session；返回 (client, db)。"""

    async def override_db():
        yield db_session

    from app.api.deps import get_db

    app.dependency_overrides[get_db] = override_db
    yield client, db_session
    app.dependency_overrides.pop(get_db, None)


def _auth_headers(customer_id: int) -> dict:
    token = create_customer_token(customer_id, "wechat")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def customer_dep():
    from app.api.deps import get_current_customer

    holder = {}

    def install(customer):
        async def override_customer():
            return SimpleNamespace(id=customer.id, status=1, token_version=0, account_status="ACTIVE")

        app.dependency_overrides[get_current_customer] = override_customer
        holder["key"] = get_current_customer

    def uninstall():
        if "key" in holder:
            app.dependency_overrides.pop(holder["key"], None)

    return SimpleNamespace(install=install, uninstall=uninstall)


@pytest.fixture
def no_redis():
    """催单限频放行（模拟 Redis NX 首次成功）。"""
    with patch("app.api.orders.get_redis", new_callable=AsyncMock) as mock_get:
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        mock_get.return_value = redis
        yield redis


@pytest.fixture
def mute_ws():
    with patch("app.api.orders.ws_manager") as mock_ws:
        mock_ws.notify_order_event = AsyncMock()
        yield mock_ws


# ---------------------------------------------------------------------------
# 催单
# ---------------------------------------------------------------------------


async def test_urge_rejected_when_status_not_in_progress(api, customer_dep, no_redis, mute_ws):
    client, db = api
    store = await _seed_store(db)
    cust = await _seed_customer(db)
    order = await _seed_order(db, store, cust, status="PAID")
    await db.commit()
    customer_dep.install(cust)
    res = await client.post(f"/api/orders/{order.id}/urge", headers=_auth_headers(cust.id))
    body = res.json()
    assert body["code"] == 40058


async def test_urge_success_increments_count(api, customer_dep, no_redis, mute_ws):
    client, db = api
    store = await _seed_store(db)
    cust = await _seed_customer(db)
    order = await _seed_order(db, store, cust, status="CONFIRMED")
    await db.commit()
    customer_dep.install(cust)
    res = await client.post(f"/api/orders/{order.id}/urge", headers=_auth_headers(cust.id))
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == 0
    assert body["data"]["urge_count"] == 1
    assert body["data"]["limited"] is False
    assert mute_ws.notify_order_event.await_count == 1
    assert mute_ws.notify_order_event.await_args[0][0] == "ORDER_URGED"


async def test_urge_rate_limited_returns_success_without_increment(api, customer_dep, mute_ws):
    client, db = api
    store = await _seed_store(db)
    cust = await _seed_customer(db)
    order = await _seed_order(db, store, cust, status="COOKING")
    await db.commit()
    customer_dep.install(cust)
    with patch("app.api.orders.get_redis", new_callable=AsyncMock) as mock_get:
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=None)  # NX 失败：5 分钟内已催过
        mock_get.return_value = redis
        res = await client.post(f"/api/orders/{order.id}/urge", headers=_auth_headers(cust.id))
    body = res.json()
    assert body["code"] == 0
    assert body["data"]["limited"] is True
    assert body["data"]["urge_count"] == 0


async def test_urge_other_customer_order_returns_40404(api, customer_dep, no_redis, mute_ws):
    client, db = api
    store = await _seed_store(db)
    owner = await _seed_customer(db)
    stranger = await _seed_customer(db)
    order = await _seed_order(db, store, owner, status="CONFIRMED")
    await db.commit()
    customer_dep.install(stranger)
    res = await client.post(f"/api/orders/{order.id}/urge", headers=_auth_headers(stranger.id))
    assert res.json()["code"] == 40404


# ---------------------------------------------------------------------------
# 支付成功领券
# ---------------------------------------------------------------------------


async def test_claimable_disabled_without_setting(api, customer_dep):
    client, db = api
    store = await _seed_store(db)
    cust = await _seed_customer(db)
    order = await _seed_order(db, store, cust, payment_status="PAID")
    await db.commit()
    customer_dep.install(cust)
    res = await client.get(f"/api/orders/{order.id}/claimable-coupon", headers=_auth_headers(cust.id))
    data = res.json()["data"]
    assert data["available"] is False
    assert data["claimed"] is False


async def test_claimable_and_claim_flow(api, customer_dep):
    client, db = api
    store = await _seed_store(db)
    cust = await _seed_customer(db)
    order = await _seed_order(db, store, cust, payment_status="PAID")
    await _seed_coupon_template(db)
    await _enable_setting(db, "pay_success_coupon_name", "支付成功券")
    await db.commit()
    customer_dep.install(cust)

    res = await client.get(f"/api/orders/{order.id}/claimable-coupon", headers=_auth_headers(cust.id))
    data = res.json()["data"]
    assert data["available"] is True
    assert data["coupon_name"] == "支付成功券"

    res = await client.post(f"/api/orders/{order.id}/claim-coupon", headers=_auth_headers(cust.id))
    body = res.json()
    assert body["code"] == 0
    assert body["data"]["claimed"] is True
    assert body["data"]["coupon"]["name"] == "支付成功券"

    # 幂等：重复领取返回 claimed=False
    res = await client.post(f"/api/orders/{order.id}/claim-coupon", headers=_auth_headers(cust.id))
    assert res.json()["data"]["claimed"] is False

    rows = list((await db.execute(__import__("sqlalchemy").select(CustomerCoupon))).scalars())
    assert len(rows) == 1


async def test_claim_rejected_when_unpaid(api, customer_dep):
    client, db = api
    store = await _seed_store(db)
    cust = await _seed_customer(db)
    order = await _seed_order(db, store, cust, payment_status="UNPAID", status="PENDING_PAYMENT")
    await _seed_coupon_template(db)
    await _enable_setting(db, "pay_success_coupon_name", "支付成功券")
    await db.commit()
    customer_dep.install(cust)
    res = await client.post(f"/api/orders/{order.id}/claim-coupon", headers=_auth_headers(cust.id))
    assert res.json()["code"] == 40059


# ---------------------------------------------------------------------------
# 自助退款
# ---------------------------------------------------------------------------


async def test_self_refund_creates_pending_request(api, customer_dep):
    client, db = api
    store = await _seed_store(db)
    cust = await _seed_customer(db)
    order = await _seed_order(db, store, cust, status="CONFIRMED", payment_status="PAID")
    await _seed_payment(db, order, cust)
    await db.commit()
    customer_dep.install(cust)
    res = await client.post(f"/api/orders/{order.id}/self-refund", headers=_auth_headers(cust.id))
    body = res.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "PENDING"

    # 幂等：重复申请返回同一 request_id
    res2 = await client.post(f"/api/orders/{order.id}/self-refund", headers=_auth_headers(cust.id))
    assert res2.json()["data"]["request_id"] == body["data"]["request_id"]


async def test_self_refund_rejects_pos_channel(api, customer_dep):
    client, db = api
    store = await _seed_store(db)
    cust = await _seed_customer(db)
    order = await _seed_order(db, store, cust, status="CONFIRMED", payment_status="PAID", channel="POS")
    await _seed_payment(db, order, cust)
    await db.commit()
    customer_dep.install(cust)
    res = await client.post(f"/api/orders/{order.id}/self-refund", headers=_auth_headers(cust.id))
    assert res.json()["code"] == 40055


async def test_self_refund_rejects_unpaid(api, customer_dep):
    client, db = api
    store = await _seed_store(db)
    cust = await _seed_customer(db)
    order = await _seed_order(db, store, cust, status="PENDING_PAYMENT", payment_status="UNPAID")
    await db.commit()
    customer_dep.install(cust)
    res = await client.post(f"/api/orders/{order.id}/self-refund", headers=_auth_headers(cust.id))
    assert res.json()["code"] == 40056


async def test_self_refund_rejects_when_cooking_started(api, customer_dep):
    client, db = api
    store = await _seed_store(db)
    cust = await _seed_customer(db)
    order = await _seed_order(db, store, cust, status="PREPARING", payment_status="PAID")
    await _seed_payment(db, order, cust)
    await db.commit()
    customer_dep.install(cust)
    res = await client.post(f"/api/orders/{order.id}/self-refund", headers=_auth_headers(cust.id))
    assert res.json()["code"] == 40057


async def test_self_refund_rejects_when_kitchen_claimed(api, customer_dep):
    client, db = api
    store = await _seed_store(db)
    cust = await _seed_customer(db)
    order = await _seed_order(db, store, cust, status="CONFIRMED", payment_status="PAID")
    order.kitchen_claimed_by = None
    order.kitchen_claimed_by = 999  # 已被后厨认领 = 已开始制作
    await db.commit()
    customer_dep.install(cust)
    res = await client.post(f"/api/orders/{order.id}/self-refund", headers=_auth_headers(cust.id))
    assert res.json()["code"] == 40057


async def test_self_refund_rejects_stranger(api, customer_dep):
    client, db = api
    store = await _seed_store(db)
    owner = await _seed_customer(db)
    stranger = await _seed_customer(db)
    order = await _seed_order(db, store, owner, status="CONFIRMED", payment_status="PAID")
    await _seed_payment(db, order, owner)
    await db.commit()
    customer_dep.install(stranger)
    res = await client.post(f"/api/orders/{order.id}/self-refund", headers=_auth_headers(stranger.id))
    assert res.json()["code"] == 40404


# ---------------------------------------------------------------------------
# 预约取餐时段校验（服务层）
# ---------------------------------------------------------------------------


async def test_scheduled_pickup_validation_window():
    from app.core.exceptions import AppException
    from unittest.mock import AsyncMock, MagicMock

    from app.services.customer_service import OrderCreateService

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

    # 过早（10 分钟内）
    early = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
    with pytest.raises(AppException) as ei:
        await OrderCreateService.create_order(
            db,
            store_id=1,
            table_token=None,
            customer_id=1,
            items=[{"product_id": 1, "quantity": 1}],
            channel="H5",
            idempotency_key=f"k-{early}",
            dining_type="TAKEAWAY",
            scheduled_pickup_at=early,
        )
    assert ei.value.code == 40062

    # 过远（3 天后）
    far = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
    with pytest.raises(AppException) as ei2:
        await OrderCreateService.create_order(
            db,
            store_id=1,
            table_token=None,
            customer_id=1,
            items=[{"product_id": 1, "quantity": 1}],
            channel="H5",
            idempotency_key=f"k-{far}",
            dining_type="TAKEAWAY",
            scheduled_pickup_at=far,
        )
    assert ei2.value.code == 40063

    # 非法格式
    with pytest.raises(AppException) as ei3:
        await OrderCreateService.create_order(
            db,
            store_id=1,
            table_token=None,
            customer_id=1,
            items=[{"product_id": 1, "quantity": 1}],
            channel="H5",
            idempotency_key="k-bad",
            dining_type="TAKEAWAY",
            scheduled_pickup_at="not-a-date",
        )
    assert ei3.value.code == 40061


# ---------------------------------------------------------------------------
# 生日双倍积分
# ---------------------------------------------------------------------------


async def test_birthday_double_points(db_session):
    cust = await _seed_customer(db_session, birthday=datetime.now().date())
    store = await _seed_store(db_session)
    order = await _seed_order(db_session, store, cust, payment_status="PAID")
    await db_session.commit()

    earned = await CustomerService.award_points_for_paid_order(db_session, order)
    assert earned == 60  # 30 元 × 2（生日双倍）
    await db_session.refresh(cust)
    assert cust.points == 60


async def test_normal_points_without_birthday(db_session):
    cust = await _seed_customer(db_session, birthday=None)
    store = await _seed_store(db_session)
    order = await _seed_order(db_session, store, cust, payment_status="PAID")
    await db_session.commit()

    earned = await CustomerService.award_points_for_paid_order(db_session, order)
    assert earned == 30
    await db_session.refresh(cust)
    assert cust.points == 30


async def test_award_points_skips_fully_refunded_order(db_session):
    """全额退后迟到的 points_award outbox 不得再发分。"""
    cust = await _seed_customer(db_session, birthday=None)
    store = await _seed_store(db_session)
    order = await _seed_order(
        db_session, store, cust, status="REFUNDED", payment_status="REFUNDED"
    )
    await db_session.commit()

    earned = await CustomerService.award_points_for_paid_order(db_session, order)
    assert earned == 0
    await db_session.refresh(cust)
    assert int(cust.points or 0) == 0


async def test_late_award_then_catchup_refund_deduct(db_session):
    """先退款（尚无 EARN）再迟到发分：须发分后按 SUCCESS 退款补扣。"""
    from app.models import PaymentRefund

    cust = await _seed_customer(db_session, birthday=None)
    store = await _seed_store(db_session)
    order = await _seed_order(
        db_session, store, cust, status="PARTIAL_REFUND", payment_status="PARTIAL_REFUND"
    )
    pay = await _seed_payment(db_session, order, cust)
    rf = PaymentRefund(
        id=next(_seq) + 9000,
        refund_no=f"R{next(_seq)}",
        payment_id=pay.id,
        order_id=order.id,
        amount=Decimal("10.00"),
        status="SUCCESS",
    )
    db_session.add(rf)
    await db_session.commit()

    # 退款时无 EARN，扣分为 0；迟到 award 发 30 后应补扣 10
    deducted0 = await CustomerService.deduct_points_for_refund(
        db_session, order, Decimal("10.00"), refund_id=int(rf.id)
    )
    assert deducted0 == 0
    earned = await CustomerService.award_points_for_paid_order(db_session, order)
    assert earned == 30
    await db_session.refresh(cust)
    assert int(cust.points or 0) == 20

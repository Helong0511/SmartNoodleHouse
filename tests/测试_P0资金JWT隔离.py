"""P0 资金/JWT/退款券回滚/库存/跨类型令牌 专项安全回归。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jose import JWTError, jwt

from app.core.exceptions import AppException
from app.core.security import (
    create_access_token,
    create_customer_token,
    create_stream_ticket,
    decode_customer_token,
    decode_token,
)
from app.core.startup_checks import validate_settings
from app.services.coupon_service import CouponService
from app.services.store_scope import assert_store_access


def _jwt_settings(**over):
    base = dict(
        jwt_secret="s" * 48,
        jwt_algorithm="HS256",
        jwt_expire_minutes=60,
        customer_jwt_expire_minutes=120,
        app_name="SmartNoodleHouse",
        jwt_audience="smart-noodle-house",
    )
    base.update(over)
    return SimpleNamespace(**base)


# ─── JWT：保留声明 / TTL / 跨类型 ───


def test_staff_ttl_honors_config_not_forced_30():
    """配置 10 分钟时实际过期应约 10 分钟，不再被强制抬到 30。"""
    with patch("app.core.security.get_settings", return_value=_jwt_settings(jwt_expire_minutes=10)), patch(
        "app.services.system_settings_service.SystemSettingsService.resolved_int_sync",
        return_value=10,
    ):
        token = create_access_token(1)
        payload = decode_token(token)
    exp = payload["exp"]
    if isinstance(exp, datetime):
        delta = exp - datetime.now(timezone.utc)
    else:
        delta = datetime.fromtimestamp(exp, tz=timezone.utc) - datetime.now(timezone.utc)
    assert timedelta(minutes=8) < delta < timedelta(minutes=12)


def test_customer_ttl_independent_from_staff():
    with patch(
        "app.core.security.get_settings",
        return_value=_jwt_settings(jwt_expire_minutes=10, customer_jwt_expire_minutes=90),
    ), patch(
        "app.services.system_settings_service.SystemSettingsService.resolved_int_sync",
        side_effect=lambda key, fallback: 90 if "customer" in key else 10,
    ):
        cust = create_customer_token(7, "wechat")
        payload = decode_token(cust)
    assert payload["typ"] == "customer"
    exp = payload["exp"]
    if isinstance(exp, datetime):
        delta = exp - datetime.now(timezone.utc)
    else:
        delta = datetime.fromtimestamp(exp, tz=timezone.utc) - datetime.now(timezone.utc)
    assert timedelta(minutes=80) < delta < timedelta(minutes=100)


def test_stream_ticket_ttl_capped_at_300s():
    with patch("app.core.security.get_settings", return_value=_jwt_settings()):
        token = create_stream_ticket(1, camera_id=9, store_id=2, ttl_seconds=9999)
        payload = decode_token(token)
    assert payload["typ"] == "stream"
    assert payload["cam"] == 9
    assert payload["store"] == 2
    exp = payload["exp"]
    if isinstance(exp, datetime):
        delta = exp - datetime.now(timezone.utc)
    else:
        delta = datetime.fromtimestamp(exp, tz=timezone.utc) - datetime.now(timezone.utc)
    assert delta <= timedelta(seconds=301)


def test_customer_channel_normalized():
    with patch("app.core.security.get_settings", return_value=_jwt_settings()), patch(
        "app.services.system_settings_service.SystemSettingsService.resolved_int_sync",
        return_value=60,
    ):
        bad = create_customer_token(1, "evil-channel")
        good_wx = create_customer_token(1, "wechat")
        good_ali = create_customer_token(1, "alipay")
        assert decode_token(bad)["channel"] == "wechat"
        assert decode_token(good_wx)["channel"] == "wechat"
        assert decode_token(good_ali)["channel"] == "alipay"


def test_cross_type_customer_token_rejected_by_decode_customer():
    with patch("app.core.security.get_settings", return_value=_jwt_settings()), patch(
        "app.services.system_settings_service.SystemSettingsService.resolved_int_sync",
        return_value=60,
    ):
        staff = create_access_token(1)
        stream = create_stream_ticket(1, camera_id=3)
    with pytest.raises(JWTError):
        decode_customer_token(staff)
    with pytest.raises(JWTError):
        decode_customer_token(stream)


@pytest.mark.asyncio
async def test_staff_api_rejects_customer_and_stream_tokens():
    """get_current_user 只接受 typ=staff。"""
    from fastapi import HTTPException

    from app.api.deps import get_current_user

    with patch("app.core.security.get_settings", return_value=_jwt_settings()), patch(
        "app.services.system_settings_service.SystemSettingsService.resolved_int_sync",
        return_value=60,
    ):
        customer = create_customer_token(99, "wechat")
        stream = create_stream_ticket(1, camera_id=1)

    req = MagicMock()
    db = AsyncMock()
    for tok in (customer, stream):
        with pytest.raises(HTTPException) as ei:
            await get_current_user(req, db, authorization=f"Bearer {tok}")
        assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_customer_api_rejects_staff_token():
    from fastapi import HTTPException

    from app.api.deps import get_current_customer

    with patch("app.core.security.get_settings", return_value=_jwt_settings()), patch(
        "app.services.system_settings_service.SystemSettingsService.resolved_int_sync",
        return_value=60,
    ):
        staff = create_access_token(1)

    req = MagicMock()
    db = AsyncMock()
    with pytest.raises(HTTPException) as ei:
        await get_current_customer(req, db, authorization=f"Bearer {staff}")
    assert ei.value.status_code == 401


def test_forged_typ_in_extra_cannot_become_customer_staff_token():
    with patch("app.core.security.get_settings", return_value=_jwt_settings()), patch(
        "app.services.system_settings_service.SystemSettingsService.resolved_int_sync",
        return_value=60,
    ):
        token = create_access_token(5, {"typ": "customer", "sub": "1", "role": "X"})
        payload = decode_token(token)
    assert payload["typ"] == "staff"
    assert payload["sub"] == "5"


# ─── 门店隔离 ───


def test_store_scope_blocks_cross_store():
    from fastapi import HTTPException

    user = SimpleNamespace(store_id=1, role=SimpleNamespace(code="CASHIER"))
    with pytest.raises(HTTPException) as ei:
        assert_store_access(user, 2)
    assert ei.value.status_code == 403


def test_store_scope_super_admin_ok():
    user = SimpleNamespace(store_id=None, role=SimpleNamespace(code="SUPER_ADMIN"))
    assert_store_access(user, 99)


# ─── 退款回滚已核销券 ───


def _exec_scalar_one(value):
    """模拟 db.execute(...).scalar_one_or_none() / scalars().all()。"""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    return result


def _exec_ids(ids, row=None):
    """第一次 execute 返回 id 列表，后续返回行锁对象。"""
    calls = {"n": 0}

    async def _execute(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            r = MagicMock()
            r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=ids)))
            r.scalar_one_or_none = MagicMock(return_value=None)
            return r
        return _exec_scalar_one(row)

    return AsyncMock(side_effect=_execute)


@pytest.mark.asyncio
async def test_release_for_order_restores_used_coupon_on_refund():
    order = SimpleNamespace(id=42, customer_coupon_id=7, customer_voucher_id=None)
    coupon = SimpleNamespace(id=7, status="USED", used_order_id=42, used_at=datetime.now())
    db = AsyncMock()
    db.execute = _exec_ids([7], row=coupon)
    db.flush = AsyncMock()

    await CouponService.release_for_order(db, order)

    assert coupon.status == "AVAILABLE"
    assert coupon.used_order_id is None
    assert coupon.used_at is None


@pytest.mark.asyncio
async def test_release_for_order_ignores_other_order_coupon():
    order = SimpleNamespace(id=42, customer_coupon_id=7, customer_voucher_id=None)
    coupon = SimpleNamespace(id=7, status="USED", used_order_id=99, used_at=datetime.now())
    db = AsyncMock()
    db.execute = _exec_ids([7], row=coupon)
    db.flush = AsyncMock()

    await CouponService.release_for_order(db, order)

    assert coupon.status == "USED"
    assert coupon.used_order_id == 99


@pytest.mark.asyncio
async def test_mark_used_noop_when_order_refunded():
    order = SimpleNamespace(
        id=42,
        status="REFUNDED",
        payment_status="REFUNDED",
        customer_coupon_id=7,
        customer_voucher_id=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()

    await CouponService.mark_used(db, order)

    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_mark_used_noop_when_coupon_already_released():
    order = SimpleNamespace(
        id=42,
        status="PAID",
        payment_status="PAID",
        customer_coupon_id=7,
        customer_voucher_id=None,
    )
    coupon = SimpleNamespace(id=7, status="AVAILABLE", used_order_id=None, used_at=None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_exec_scalar_one(coupon))
    db.flush = AsyncMock()

    await CouponService.mark_used(db, order)

    assert coupon.status == "AVAILABLE"


@pytest.mark.asyncio
async def test_reconcile_refunded_releases_coupon():
    order = SimpleNamespace(
        id=42,
        status="REFUNDED",
        payment_status="REFUNDED",
        customer_coupon_id=7,
        customer_voucher_id=None,
    )
    with patch.object(CouponService, "release_for_order", new_callable=AsyncMock) as rel, patch.object(
        CouponService, "mark_used", new_callable=AsyncMock
    ) as mk:
        await CouponService.reconcile_for_order(AsyncMock(), order)
    rel.assert_awaited_once()
    mk.assert_not_called()


@pytest.mark.asyncio
async def test_refunded_transition_releases_used_coupon():
    """订单进入 REFUNDED 时必须回滚已核销券（回归：此前仅 CANCELLED+RESERVED 会释放）。"""
    from app.services.order_lifecycle import apply_order_status

    order = MagicMock()
    order.id = 55
    order.status = "PAID"
    order.payment_status = "PAID"
    order.customer_coupon_id = 3
    order.customer_voucher_id = None
    order.cancelled_at = None

    locked = MagicMock()
    locked.scalar_one_or_none = MagicMock(return_value="PAID")
    db = MagicMock()
    db.execute = AsyncMock(return_value=locked)
    db.flush = AsyncMock()

    with patch(
        "app.services.order_lifecycle._restore_order_stock", new_callable=AsyncMock
    ) as restore_stock, patch(
        "app.services.order_lifecycle.record_order_status", new_callable=AsyncMock
    ), patch(
        "app.services.coupon_service.CouponService.release_for_order", new_callable=AsyncMock
    ) as release:
        await apply_order_status(
            db, order, "REFUNDED", source="PAYMENT", payment_status="REFUNDED", assert_kind="SYSTEM"
        )

    release.assert_awaited_once()
    restore_stock.assert_awaited_once()
    assert order.status == "REFUNDED"


# ─── 并发支付：已有 PENDING 不得再开新流程 ───


@pytest.mark.asyncio
async def test_create_payment_blocks_second_pending_channel():
    from app.services.payment_service import PaymentService

    order = SimpleNamespace(
        id=1,
        order_no="O1",
        status="PENDING_PAYMENT",
        payment_status="UNPAID",
        total_amount=Decimal("20.00"),
        customer_id=9,
        store_id=1,
        table_id=None,
        channel="WECHAT",
        dining_type="DINE_IN",
    )
    pending = SimpleNamespace(
        id=100,
        provider="WECHAT",
        channel="WECHAT_MINIPROGRAM",
        amount=Decimal("20.00"),
        status="PENDING",
    )
    locked = MagicMock()
    locked.scalar_one_or_none = MagicMock(return_value=order)
    pending_result = MagicMock()
    pending_result.scalars.return_value.all.return_value = [pending]

    calls = {"n": 0}

    async def execute(stmt):
        calls["n"] += 1
        if calls["n"] == 1:
            return locked
        return pending_result

    db = AsyncMock()
    db.execute = execute

    with patch.object(
        PaymentService, "remaining_amount", new_callable=AsyncMock, return_value=Decimal("20.00")
    ):
        with pytest.raises(AppException) as ei:
            await PaymentService.create_payment(
                db,
                order,
                provider="ALIPAY",
                channel_type="miniprogram",
                customer_id=9,
            )
    assert ei.value.code == 40908


# ─── 库存：条件更新失败即拒绝（防负库存） ───


@pytest.mark.asyncio
async def test_deduct_stock_race_loser_gets_40031():
    from app.services.stock_service import deduct_stock

    db = AsyncMock()
    result = MagicMock()
    result.rowcount = 0  # 并发下 WHERE stock>=qty 未命中
    db.execute = AsyncMock(return_value=result)
    with pytest.raises(AppException) as ei:
        await deduct_stock(db, product_id=1, qty=1, store_id=1)
    assert ei.value.code == 40031


# ─── 生产禁止 mock ───


def test_prod_startup_rejects_payment_mock():
    from tests.测试_启动打印防护 import _base_prod

    with pytest.raises(SystemExit):
        validate_settings(_base_prod(allow_payment_mock=True))


# ─── 余额退款回补幂等键 ───


@pytest.mark.asyncio
async def test_balance_restore_for_refund_is_idempotent_by_refund_id():
    from app.services.balance_service import BalanceService

    existing = SimpleNamespace(id=1, entry_type="REFUND_RESTORE")
    first = MagicMock()
    first.scalar_one_or_none = MagicMock(return_value=existing)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=first)

    out = await BalanceService.restore_for_refund(
        db,
        customer_id=1,
        order_id=2,
        refund_id=9,
        amount=Decimal("5.00"),
        store_id=1,
    )
    assert out is existing

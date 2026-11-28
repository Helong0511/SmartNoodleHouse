"""券模板门店隔离单元测试。"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.exceptions import AppException
from app.services.coupon_service import CouponService


def test_template_visible_hq_and_store():
    hq = SimpleNamespace(store_id=None)
    s1 = SimpleNamespace(store_id=1)
    s2 = SimpleNamespace(store_id=2)
    assert CouponService.template_visible_to_store(hq, None) is True
    assert CouponService.template_visible_to_store(s1, None) is True
    assert CouponService.template_visible_to_store(hq, 1) is True
    assert CouponService.template_visible_to_store(s1, 1) is True
    assert CouponService.template_visible_to_store(s2, 1) is False


# ── P2-4 总部模板门店白名单 ─────────────────────────────────────────────────


def test_hq_whitelist_restricts_visibility():
    hq = SimpleNamespace(store_id=None, store_whitelist=[1, 3])
    assert CouponService.template_visible_to_store(hq, 1) is True
    assert CouponService.template_visible_to_store(hq, 3) is True
    assert CouponService.template_visible_to_store(hq, 2) is False
    # 超管全店视角仍可见
    assert CouponService.template_visible_to_store(hq, None) is True


def test_hq_whitelist_null_means_all_stores():
    hq = SimpleNamespace(store_id=None, store_whitelist=None)
    assert CouponService.template_visible_to_store(hq, 99) is True


def test_hq_whitelist_empty_list_means_all_stores():
    hq = SimpleNamespace(store_id=None, store_whitelist=[])
    assert CouponService.template_visible_to_store(hq, 99) is True


def test_normalize_whitelist_dedup_and_positive():
    assert CouponService._normalize_whitelist(None) is None
    assert CouponService._normalize_whitelist([]) is None
    assert CouponService._normalize_whitelist([3, 1, 3, 2, 0, -5]) == [1, 2, 3]


def test_normalize_whitelist_rejects_invalid():
    with pytest.raises(AppException) as ei:
        CouponService._normalize_whitelist("not-a-list")
    assert ei.value.code == 40069


def test_template_payload_includes_store_scope():
    tmpl = MagicMock()
    tmpl.id = 1
    tmpl.store_id = None
    tmpl.name = "新客满20减3"
    tmpl.coupon_type = "FIXED"
    tmpl.discount_value = "3"
    tmpl.min_order_amount = "20"
    tmpl.valid_days = 30
    tmpl.status = 1
    tmpl.created_at = None
    payload = CouponService.template_payload(tmpl)
    assert payload["store_id"] is None
    assert payload["store_scope"] == "HQ"

    tmpl.store_id = 3
    payload2 = CouponService.template_payload(tmpl)
    assert payload2["store_id"] == 3
    assert payload2["store_scope"] == "STORE"


@pytest.mark.asyncio
async def test_template_benefit_change_blocked_while_available_coupon_exists(db_session):
    from datetime import datetime, timedelta
    from decimal import Decimal

    from app.models import CouponTemplate, Customer, CustomerCoupon

    customer = Customer(customer_no="CU_TPL_LOCK", nickname="模板锁测试", points=0)
    template = CouponTemplate(
        name="锁定权益测试",
        coupon_type="FIXED",
        discount_value=Decimal("5.00"),
        min_order_amount=Decimal("30.00"),
        valid_days=30,
        status=1,
    )
    db_session.add_all([customer, template])
    await db_session.flush()
    coupon = CustomerCoupon(
        customer_id=customer.id,
        template_id=template.id,
        coupon_no="CP-TEMPLATE-LOCK-0001",
        status="AVAILABLE",
        expires_at=datetime.now() + timedelta(days=20),
    )
    db_session.add(coupon)
    await db_session.flush()

    with pytest.raises(AppException) as exc_info:
        await CouponService.update_template(
            db_session, template, {"discount_value": Decimal("9.00")}
        )
    assert "不能修改" in exc_info.value.message
    await db_session.rollback()


@pytest.mark.asyncio
async def test_template_name_change_allowed_while_available_coupon_exists(db_session):
    from datetime import datetime, timedelta
    from decimal import Decimal

    from app.models import CouponTemplate, Customer, CustomerCoupon

    customer = Customer(customer_no="CU_TPL_NAME", nickname="模板改名测试", points=0)
    template = CouponTemplate(
        name="旧名称",
        coupon_type="FIXED",
        discount_value=Decimal("5.00"),
        min_order_amount=Decimal("30.00"),
        valid_days=30,
        status=1,
    )
    db_session.add_all([customer, template])
    await db_session.flush()
    db_session.add(
        CustomerCoupon(
            customer_id=customer.id,
            template_id=template.id,
            coupon_no="CP-TEMPLATE-NAME-0001",
            status="AVAILABLE",
            expires_at=datetime.now() + timedelta(days=20),
        )
    )
    await db_session.flush()

    updated = await CouponService.update_template(
        db_session, template, {"name": "新名称"}
    )
    assert updated.name == "新名称"
    await db_session.rollback()


@pytest.mark.asyncio
async def test_disabled_template_does_not_revoke_existing_coupon(db_session):
    from datetime import datetime, timedelta
    from decimal import Decimal

    from app.models import CouponTemplate, Customer, CustomerCoupon

    customer = Customer(customer_no="CU_TPL_DISABLED", nickname="模板停用测试", points=0)
    template = CouponTemplate(
        name="停用后仍可用",
        coupon_type="FIXED",
        discount_value=Decimal("5.00"),
        min_order_amount=Decimal("30.00"),
        valid_days=30,
        status=1,
    )
    db_session.add_all([customer, template])
    await db_session.flush()
    coupon = CustomerCoupon(
        customer_id=customer.id,
        template_id=template.id,
        coupon_no="CP-TEMPLATE-DISABLED-0001",
        status="AVAILABLE",
        expires_at=datetime.now() + timedelta(days=20),
    )
    db_session.add(coupon)
    await db_session.flush()

    # 停用只阻止继续发券，不应让已经发出的有效券从顾客可用列表消失。
    await CouponService.update_template(db_session, template, {"status": 0})
    available = await CouponService.list_available(db_session, customer.id)
    assert any(item.get("id") == coupon.id for item in available)
    await db_session.rollback()

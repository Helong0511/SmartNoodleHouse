"""稳定身份、可信手机号、会员/支付幂等安全回归。"""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.exceptions import AppException
from app.models import Customer, CustomerIdentity, Order, Payment
from app.services.customer_service import CustomerService, OrderCreateService
from app.services.payment_service import PaymentService


@pytest.mark.asyncio
async def test_manual_phone_is_unverified_and_never_merges(db_session):
    owner = Customer(
        id=1101,
        customer_no="CU_PHONE_OWNER",
        status=1,
        phone="13800001111",
        phone_verified=1,
    )
    guest = Customer(id=1102, customer_no="CU_PHONE_GUEST", status=1)
    db_session.add_all([owner, guest])
    db_session.add(
        CustomerIdentity(
            customer_id=owner.id,
            provider="PHONE",
            provider_subject="13800001111",
            verified=1,
        )
    )
    await db_session.flush()

    result = await CustomerService.save_unverified_phone(db_session, guest, "13800001111")

    assert result.id == guest.id
    assert result.phone == "13800001111"
    assert result.phone_verified == 0
    assert guest.status == 1
    identity = (
        await db_session.execute(
            select(CustomerIdentity).where(
                CustomerIdentity.provider == "PHONE",
                CustomerIdentity.provider_subject == "13800001111",
            )
        )
    ).scalar_one()
    assert identity.customer_id == owner.id


@pytest.mark.asyncio
async def test_verified_phone_conflict_requires_explicit_confirmation(db_session):
    owner = Customer(
        id=1111,
        customer_no="CU_VERIFIED_OWNER",
        status=1,
        phone="13900001111",
        phone_verified=1,
    )
    guest = Customer(id=1112, customer_no="CU_VERIFIED_GUEST", status=1)
    db_session.add_all([owner, guest])
    db_session.add(
        CustomerIdentity(
            customer_id=owner.id,
            provider="PHONE",
            provider_subject="13900001111",
            verified=1,
        )
    )
    await db_session.flush()

    with pytest.raises(AppException) as exc:
        await CustomerService.bind_verified_phone(
            db_session,
            guest,
            "13900001111",
            verification_source="WECHAT",
        )

    assert exc.value.code == 40906
    assert exc.value.status_code == 409
    assert guest.status == 1


@pytest.mark.asyncio
async def test_wechat_unionid_unifies_multiple_openids(db_session):
    first = await CustomerService.get_or_create_by_wechat(
        db_session, "wx_openid_first", "wx_union_same"
    )
    second = await CustomerService.get_or_create_by_wechat(
        db_session, "wx_openid_second", "wx_union_same"
    )

    assert second.id == first.id
    subjects = set(
        (
            await db_session.execute(
                select(CustomerIdentity.provider, CustomerIdentity.provider_subject).where(
                    CustomerIdentity.customer_id == first.id
                )
            )
        ).all()
    )
    assert ("WECHAT_OPENID", "wx_openid_first") in subjects
    assert ("WECHAT_OPENID", "wx_openid_second") in subjects
    assert ("WECHAT_UNIONID", "wx_union_same") in subjects


@pytest.mark.asyncio
async def test_member_activation_is_idempotent(db_session):
    guest = Customer(id=1121, customer_no="CU_ACTIVATE_ONCE", status=1, member_status="GUEST")
    db_session.add(guest)
    await db_session.flush()

    member, first = await CustomerService.activate_member(
        db_session, guest, channel=None, mark_channel_completed=False
    )
    member_again, second = await CustomerService.activate_member(
        db_session, member, channel=None, mark_channel_completed=False
    )

    assert first is True
    assert second is False
    assert member_again.member_status == "MEMBER"
    assert member_again.member_activated_at is not None


def test_long_idempotency_keys_do_not_collide_by_prefix():
    prefix = "x" * 100
    first = OrderCreateService._scoped_idempotency_key(
        prefix + "A", channel="WECHAT", customer_id=1, store_id=1, operator_id=None
    )
    second = OrderCreateService._scoped_idempotency_key(
        prefix + "B", channel="WECHAT", customer_id=1, store_id=1, operator_id=None
    )
    assert first != second
    assert len(first) <= 64
    assert len(second) <= 64


@pytest.mark.asyncio
async def test_second_pending_payment_flow_is_rejected(db_session):
    order = Order(
        id=1131,
        order_no="OT_PENDING_FLOW",
        store_id=1,
        status="PENDING_PAYMENT",
        payment_status="UNPAID",
        subtotal=Decimal("20.00"),
        discount_amount=Decimal("0.00"),
        total_amount=Decimal("20.00"),
        packing_fee=Decimal("0.00"),
        dining_type="TAKEAWAY",
        channel="WECHAT",
    )
    pending = Payment(
        id=1132,
        payment_no="PAY_EXISTING_FLOW",
        order_id=order.id,
        provider="WECHAT",
        channel="WECHAT_MINIPROGRAM",
        amount=Decimal("20.00"),
        status="PENDING",
    )
    db_session.add_all([order, pending])
    await db_session.flush()

    with pytest.raises(AppException) as exc:
        await PaymentService.create_payment(
            db_session,
            order,
            "ALIPAY",
            "miniprogram",
            customer_id=None,
        )

    assert exc.value.code == 40908
    assert exc.value.status_code == 409

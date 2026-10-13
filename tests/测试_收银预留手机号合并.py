"""收银预留手机号：建壳账号；官方绑号后合并积分与公益身份。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.exceptions import AppException
from app.models import (
    Customer,
    CustomerSocialIdentity,
    PointLedger,
    SocialIdentityType,
)
from app.services.customer_merge_service import CustomerMergeService
from app.services.customer_service import CustomerService


@pytest.mark.asyncio
async def test_remember_pos_phone_creates_shell(db_session):
    from app.models import Store

    store = Store(store_no="POSREM01", name="预留店", status=1)
    db_session.add(store)
    await db_session.flush()

    c1, created = await CustomerService.remember_pos_phone(
        db_session, "13800138001", store_id=int(store.id)
    )
    await db_session.commit()
    assert created is True
    assert c1.phone == "13800138001"
    assert int(c1.phone_verified or 0) == 0
    assert (c1.display_name or "").startswith("收银预留")
    assert c1.member_status == "GUEST"

    c2, created2 = await CustomerService.remember_pos_phone(
        db_session, "13800138001", store_id=int(store.id)
    )
    assert created2 is False
    assert int(c2.id) == int(c1.id)


@pytest.mark.asyncio
async def test_bind_verified_phone_merges_pos_shell_points_and_social(db_session):
    from app.models import Store

    store = Store(store_no="POSREM02", name="合并店", status=1)
    db_session.add(store)
    await db_session.flush()

    db_session.add(
        SocialIdentityType(code="SANITATION", name="环卫工", sort_order=1, status=1)
    )
    await db_session.flush()

    shell, _ = await CustomerService.remember_pos_phone(
        db_session, "13900139002", store_id=int(store.id)
    )
    shell.points = 88
    db_session.add(
        PointLedger(
            customer_id=shell.id,
            order_id=None,
            refund_id=None,
            delta=88,
            balance_after=88,
            entry_type="EARN",
        )
    )
    db_session.add(
        CustomerSocialIdentity(
            customer_id=shell.id,
            identity_code="SANITATION",
            store_id=int(store.id),
            status="ACTIVE",
            verify_method="POS",
            credential_note="收银核验",
            valid_from=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    mini = CustomerService._new_guest_customer()
    mini.display_name = "扫码客"
    db_session.add(mini)
    await db_session.flush()

    merged_customer, merged = await CustomerService.bind_verified_phone(
        db_session,
        mini,
        "13900139002",
        verification_source="WECHAT",
        confirm_merge=False,
    )
    await db_session.commit()

    assert merged is True
    assert int(merged_customer.id) == int(mini.id)
    assert int(merged_customer.phone_verified or 0) == 1
    assert int(merged_customer.points or 0) == 88

    shell_after = await db_session.get(Customer, shell.id)
    assert shell_after is not None
    assert int(shell_after.status or 0) == 0

    social = (
        await db_session.execute(
            select(CustomerSocialIdentity).where(
                CustomerSocialIdentity.customer_id == mini.id,
                CustomerSocialIdentity.identity_code == "SANITATION",
                CustomerSocialIdentity.status == "ACTIVE",
            )
        )
    ).scalar_one_or_none()
    assert social is not None


@pytest.mark.asyncio
async def test_merge_social_keeps_target_active(db_session):
    target = CustomerService._new_guest_customer()
    source = CustomerService._new_guest_customer()
    db_session.add_all([target, source])
    await db_session.flush()
    db_session.add(
        SocialIdentityType(code="ELDER", name="长者", sort_order=1, status=1)
    )
    await db_session.flush()
    db_session.add(
        CustomerSocialIdentity(
            customer_id=target.id,
            identity_code="ELDER",
            status="ACTIVE",
            verify_method="POS",
        )
    )
    db_session.add(
        CustomerSocialIdentity(
            customer_id=source.id,
            identity_code="ELDER",
            status="ACTIVE",
            verify_method="POS",
        )
    )
    await db_session.flush()

    result = await CustomerMergeService.merge(
        db_session,
        target_customer_id=int(target.id),
        source_customer_id=int(source.id),
        reason="单测",
    )
    await db_session.commit()
    assert result["stats"]["social_identities_moved"] >= 1
    assert result["stats"]["social_identities_revoked"] >= 1

    rows = list(
        (
            await db_session.execute(
                select(CustomerSocialIdentity).where(
                    CustomerSocialIdentity.customer_id == target.id
                )
            )
        ).scalars().all()
    )
    active = [r for r in rows if r.status == "ACTIVE" and r.identity_code == "ELDER"]
    revoked = [r for r in rows if r.status == "REVOKED" and r.identity_code == "ELDER"]
    assert len(active) == 1
    assert len(revoked) == 1


@pytest.mark.asyncio
async def test_remember_rejects_bad_phone(db_session):
    with pytest.raises(AppException) as ei:
        await CustomerService.remember_pos_phone(db_session, "12345", store_id=1)
    assert ei.value.code == 40053

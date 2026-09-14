"""顾客合并服务：账号迁移、券冲突、积分合并。"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import count
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import (
    CouponTemplate,
    Customer,
    CustomerAlipayAccount,
    CustomerCoupon,
    CustomerWechatAccount,
    MemberLevel,
    PointLedger,
)
from app.services.customer_merge_service import CustomerMergeService

_TZ = timezone(timedelta(hours=8))


def _sqlite_auto_id(db_session, model, start: int = 7000):
    ids = count(start)
    real_add = db_session.add

    def _add(obj):
        if isinstance(obj, model) and getattr(obj, "id", None) is None:
            obj.id = next(ids)
        return real_add(obj)

    db_session.add = _add  # type: ignore[method-assign]


async def _seed_levels(db):
    lv = MemberLevel(id=1, name="普通会员", min_points=0, discount_rate=Decimal("1.0000"))
    silver = MemberLevel(id=2, name="银卡会员", min_points=100, discount_rate=Decimal("0.9800"))
    db.add_all([lv, silver])
    await db.flush()
    return lv, silver


@pytest.mark.asyncio
async def test_merge_moves_accounts_and_points(db_session):
    await _seed_levels(db_session)
    target = Customer(
        id=901,
        customer_no="CU_MG_T",
        status=1,
        phone="13800009001",
        points=40,
        member_level_id=1,
    )
    source = Customer(
        id=902,
        customer_no="CU_MG_S",
        status=1,
        phone="13800009002",
        points=80,
        member_level_id=1,
    )
    db_session.add_all([target, source])
    await db_session.flush()
    db_session.add(
        CustomerWechatAccount(
            id=31, customer_id=source.id, openid="wx_merge_src_1", profile_completed=1
        )
    )
    db_session.add(
        CustomerAlipayAccount(
            id=41, customer_id=source.id, alipay_user_id="ali_merge_src_1", profile_completed=1
        )
    )
    db_session.add(
        CustomerWechatAccount(
            id=32, customer_id=target.id, openid="wx_target_keep", profile_completed=1
        )
    )
    await db_session.flush()
    _sqlite_auto_id(db_session, PointLedger, 7100)

    with patch(
        "app.services.customer_merge_service.AuditService.log",
        new_callable=AsyncMock,
    ) as mock_audit:
        result = await CustomerMergeService.merge(
            db_session,
            target_customer_id=target.id,
            source_customer_id=source.id,
            reason="测试合并",
            operator_id=1,
        )
        await db_session.flush()
        mock_audit.assert_awaited()
        detail = mock_audit.await_args.kwargs.get("detail") or {}
        before_phone = ((detail.get("before") or {}).get("target") or {}).get("phone")
        assert before_phone == "138****9001"

    assert result["stats"]["wechat_moved"] == 1
    assert result["stats"]["alipay_moved"] == 1
    assert result["stats"]["points_added"] == 80

    merge_rows = (
        await db_session.execute(
            select(PointLedger).where(
                PointLedger.customer_id == target.id, PointLedger.entry_type == "MERGE"
            )
        )
    ).scalars().all()
    assert len(merge_rows) == 1
    assert int(merge_rows[0].delta) == 80

    await db_session.refresh(target)
    await db_session.refresh(source)
    assert int(target.points) == 120
    assert int(target.member_level_id) == 2
    assert int(source.status) == 0
    assert source.phone is None

    wx = (
        await db_session.execute(
            select(CustomerWechatAccount).where(CustomerWechatAccount.customer_id == target.id)
        )
    ).scalars().all()
    assert {w.openid for w in wx} == {"wx_merge_src_1", "wx_target_keep"}

    ali = (
        await db_session.execute(
            select(CustomerAlipayAccount).where(
                CustomerAlipayAccount.alipay_user_id == "ali_merge_src_1"
            )
        )
    ).scalar_one()
    assert ali.customer_id == target.id


@pytest.mark.asyncio
async def test_merge_moves_all_coupons_including_same_template(db_session):
    await _seed_levels(db_session)
    target = Customer(id=911, customer_no="CU_MG_CT", status=1, points=0)
    source = Customer(id=912, customer_no="CU_MG_CS", status=1, points=0)
    db_session.add_all([target, source])
    tmpl = CouponTemplate(
        id=51,
        name="合并测试券",
        coupon_type="FIXED",
        discount_value=Decimal("5"),
        min_order_amount=Decimal("20"),
        valid_days=30,
        status=1,
    )
    tmpl2 = CouponTemplate(
        id=52,
        name="仅源券",
        coupon_type="FIXED",
        discount_value=Decimal("3"),
        min_order_amount=Decimal("10"),
        valid_days=30,
        status=1,
    )
    db_session.add_all([tmpl, tmpl2])
    await db_session.flush()

    expires = datetime.now(_TZ) + timedelta(days=10)
    db_session.add(
        CustomerCoupon(
            id=61,
            customer_id=target.id,
            template_id=tmpl.id,
            coupon_no="CP_MG_T1",
            status="AVAILABLE",
            expires_at=expires,
        )
    )
    db_session.add(
        CustomerCoupon(
            id=62,
            customer_id=source.id,
            template_id=tmpl.id,
            coupon_no="CP_MG_S1",
            status="AVAILABLE",
            expires_at=expires,
        )
    )
    db_session.add(
        CustomerCoupon(
            id=63,
            customer_id=source.id,
            template_id=tmpl2.id,
            coupon_no="CP_MG_S2",
            status="AVAILABLE",
            expires_at=expires,
        )
    )
    await db_session.flush()

    with patch(
        "app.services.customer_merge_service.AuditService.log",
        new_callable=AsyncMock,
    ):
        result = await CustomerMergeService.merge(
            db_session,
            target_customer_id=target.id,
            source_customer_id=source.id,
            reason="券合并",
        )
        await db_session.flush()

    assert result["stats"]["coupons_expired_dup"] == 0
    assert result["stats"]["coupons_moved"] == 2

    for no in ("CP_MG_S1", "CP_MG_S2", "CP_MG_T1"):
        row = (
            await db_session.execute(select(CustomerCoupon).where(CustomerCoupon.coupon_no == no))
        ).scalar_one()
        assert row.customer_id == target.id
        assert row.status == "AVAILABLE"

"""展示名（员工侧）与昵称（渠道侧）写入/同步规则。"""
from __future__ import annotations

import pytest


def test_staff_customer_name_fields_keeps_display_and_nick_separate():
    from types import SimpleNamespace

    from app.services.customer_service import CustomerService

    empty = CustomerService.staff_customer_name_fields(None)
    assert empty["customer_display_name"] is None
    assert empty["customer_nickname"] is None
    assert empty["customer_name"] is None

    both = CustomerService.staff_customer_name_fields(
        SimpleNamespace(display_name="店内张三", nickname="微信小张")
    )
    assert both["customer_display_name"] == "店内张三"
    assert both["customer_nickname"] == "微信小张"
    assert both["customer_name"] == "店内张三"

    nick_only = CustomerService.staff_customer_name_fields(
        SimpleNamespace(display_name=None, nickname="仅昵称")
    )
    assert nick_only["customer_display_name"] is None
    assert nick_only["customer_nickname"] == "仅昵称"
    assert nick_only["customer_name"] == "仅昵称"


def test_fill_customer_display_if_empty_keeps_fields_independent():
    from types import SimpleNamespace

    from app.services.customer_service import CustomerService

    # 已有员工展示名、昵称为空：只补昵称
    c = SimpleNamespace(display_name="店内尊称", nickname=None, avatar_url=None)
    CustomerService.fill_customer_display_if_empty(c, nickname="渠道昵称")
    assert c.nickname == "渠道昵称"
    assert c.display_name == "店内尊称"

    # 两者皆空：两边都补
    c2 = SimpleNamespace(display_name=None, nickname=None, avatar_url=None)
    CustomerService.fill_customer_display_if_empty(c2, nickname="新人")
    assert c2.nickname == "新人"
    assert c2.display_name == "新人"

    # 已有昵称：不覆盖
    c3 = SimpleNamespace(display_name="店内", nickname="旧昵称", avatar_url=None)
    CustomerService.fill_customer_display_if_empty(c3, nickname="新昵称")
    assert c3.nickname == "旧昵称"
    assert c3.display_name == "店内"


def test_apply_miniprogram_nickname_does_not_overwrite_custom_display():
    from types import SimpleNamespace

    from app.services.customer_service import CustomerService

    # 员工定制展示名：小程序改昵称不得覆盖
    c = SimpleNamespace(display_name="店内尊称", nickname="旧昵称")
    assert CustomerService.apply_miniprogram_nickname(c, "新昵称") is True
    assert c.nickname == "新昵称"
    assert c.display_name == "店内尊称"

    # 展示名当初由昵称拷贝：跟随更新
    c2 = SimpleNamespace(display_name="阿面", nickname="阿面")
    assert CustomerService.apply_miniprogram_nickname(c2, "小面") is True
    assert c2.nickname == "小面"
    assert c2.display_name == "小面"

    # 展示名为空：补上
    c3 = SimpleNamespace(display_name=None, nickname=None)
    assert CustomerService.apply_miniprogram_nickname(c3, "新人") is True
    assert c3.nickname == "新人"
    assert c3.display_name == "新人"

    # 无效昵称
    c4 = SimpleNamespace(display_name="A", nickname="B")
    assert CustomerService.apply_miniprogram_nickname(c4, "  ") is False
    assert c4.nickname == "B"


@pytest.mark.asyncio
async def test_admin_nickname_change_syncs_channels_display_does_not(db_session):
    """后台只改展示名不同步渠道；改昵称才同步两端。"""
    from sqlalchemy import select

    from app.models import Customer, CustomerAlipayAccount, CustomerWechatAccount
    from app.services.customer_service import CustomerService

    customer = Customer(
        id=9201,
        customer_no="CU_NAME_SPLIT",
        status=1,
        nickname="渠道旧昵称",
        display_name="旧展示",
    )
    db_session.add(customer)
    await db_session.flush()
    db_session.add(
        CustomerWechatAccount(
            id=92011,
            customer_id=customer.id,
            openid="wx_name_split",
            profile_completed=1,
            nickname="渠道旧昵称",
        )
    )
    db_session.add(
        CustomerAlipayAccount(
            id=92012,
            customer_id=customer.id,
            alipay_user_id="ali_name_split",
            profile_completed=1,
            nickname="渠道旧昵称",
        )
    )
    await db_session.flush()

    # 只改展示名：不调用 sync 时渠道应保持；显式调用 sync 也只推 nickname 参数
    customer.display_name = "新展示名"
    await CustomerService.sync_nickname_to_channel_accounts(
        db_session, customer, nickname=customer.nickname
    )
    await db_session.flush()
    wx = (
        await db_session.execute(
            select(CustomerWechatAccount).where(CustomerWechatAccount.customer_id == customer.id)
        )
    ).scalar_one()
    assert wx.nickname == "渠道旧昵称"

    customer.nickname = "渠道新昵称"
    await CustomerService.sync_nickname_to_channel_accounts(
        db_session, customer, nickname=customer.nickname
    )
    await db_session.flush()
    wx2 = (
        await db_session.execute(
            select(CustomerWechatAccount).where(CustomerWechatAccount.customer_id == customer.id)
        )
    ).scalar_one()
    ali2 = (
        await db_session.execute(
            select(CustomerAlipayAccount).where(CustomerAlipayAccount.customer_id == customer.id)
        )
    ).scalar_one()
    assert wx2.nickname == "渠道新昵称"
    assert ali2.nickname == "渠道新昵称"
    assert customer.display_name == "新展示名"

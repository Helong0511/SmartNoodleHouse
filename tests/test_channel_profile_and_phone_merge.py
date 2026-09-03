"""微信/支付宝分渠道注册：未完成本端引导时不得沿用对端资料；昵称头像按渠道分存。"""
import pytest
from sqlalchemy import select

from app.models import Customer, CustomerAlipayAccount, CustomerWechatAccount
from app.services.customer_service import CustomerService


@pytest.mark.asyncio
async def test_alipay_channel_not_completed_when_only_wechat_registered(db_session):
    customer = Customer(
        id=801,
        customer_no="CU_CH_001",
        status=1,
        nickname="微信张三",
        display_name="微信张三",
        avatar_url="/uploads/avatars/wx.jpg",
        phone="13800001111",
    )
    db_session.add(customer)
    await db_session.flush()
    db_session.add(
        CustomerWechatAccount(
            id=11,
            customer_id=customer.id,
            openid="wx_real_001",
            profile_completed=1,
            nickname="微信张三",
            avatar_url="/uploads/avatars/wx.jpg",
        )
    )
    db_session.add(
        CustomerAlipayAccount(id=21, customer_id=customer.id, alipay_user_id="ali_real_001", profile_completed=0)
    )
    await db_session.flush()

    assert await CustomerService.is_channel_profile_completed(db_session, customer.id, "wechat") is True
    assert await CustomerService.is_channel_profile_completed(db_session, customer.id, "alipay") is False
    ali = await CustomerService.resolve_channel_profile(db_session, customer, "alipay")
    assert ali["completed"] is False
    assert ali["nickname"] is None
    wx = await CustomerService.resolve_channel_profile(db_session, customer, "wechat")
    assert wx["nickname"] == "微信张三"
    assert wx["avatar_url"] == "/uploads/avatars/wx.jpg"


@pytest.mark.asyncio
async def test_try_link_by_phone_keeps_each_channel_profile(db_session):
    wechat = Customer(
        id=802,
        customer_no="CU_CH_WX",
        status=1,
        nickname="微信张三",
        display_name="微信张三",
        avatar_url="/uploads/avatars/wx.jpg",
        phone="13800002222",
    )
    alipay = Customer(
        id=803,
        customer_no="CU_CH_ALI",
        status=1,
        nickname="支付宝李四",
        display_name="支付宝李四",
        avatar_url="/uploads/avatars/ali.jpg",
    )
    db_session.add_all([wechat, alipay])
    await db_session.flush()
    db_session.add(
        CustomerWechatAccount(
            id=12,
            customer_id=wechat.id,
            openid="wx_real_002",
            profile_completed=1,
            nickname="微信张三",
            avatar_url="/uploads/avatars/wx.jpg",
        )
    )
    db_session.add(
        CustomerAlipayAccount(
            id=22,
            customer_id=alipay.id,
            alipay_user_id="ali_real_002",
            profile_completed=0,
            nickname="支付宝李四",
            avatar_url="/uploads/avatars/ali.jpg",
        )
    )
    await db_session.flush()

    merged = await CustomerService.try_link_by_phone(db_session, alipay, "13800002222")
    await db_session.flush()

    assert merged.id == wechat.id
    assert merged.nickname == "微信张三"
    assert merged.avatar_url == "/uploads/avatars/wx.jpg"
    assert int(alipay.status) == 0

    ali_acc = (
        await db_session.execute(
            select(CustomerAlipayAccount).where(CustomerAlipayAccount.alipay_user_id == "ali_real_002")
        )
    ).scalar_one()
    assert ali_acc.customer_id == wechat.id
    assert ali_acc.nickname == "支付宝李四"
    assert ali_acc.avatar_url == "/uploads/avatars/ali.jpg"

    wx_acc = (
        await db_session.execute(
            select(CustomerWechatAccount).where(CustomerWechatAccount.openid == "wx_real_002")
        )
    ).scalar_one()
    assert wx_acc.nickname == "微信张三"
    assert wx_acc.avatar_url == "/uploads/avatars/wx.jpg"

    await CustomerService.save_channel_profile(
        db_session,
        merged.id,
        "alipay",
        nickname="支付宝李四",
        avatar_url="/uploads/avatars/ali.jpg",
        completed=True,
    )
    wx_view = await CustomerService.resolve_channel_profile(db_session, merged, "wechat")
    ali_view = await CustomerService.resolve_channel_profile(db_session, merged, "alipay")
    assert wx_view["nickname"] == "微信张三"
    assert wx_view["avatar_url"] == "/uploads/avatars/wx.jpg"
    assert ali_view["nickname"] == "支付宝李四"
    assert ali_view["avatar_url"] == "/uploads/avatars/ali.jpg"
    assert merged.nickname == "微信张三"


@pytest.mark.asyncio
async def test_save_channel_profile_does_not_overwrite_other_channel(db_session):
    customer = Customer(
        id=804,
        customer_no="CU_CH_BOTH",
        status=1,
        nickname="微信张三",
        display_name="微信张三",
        avatar_url="/uploads/avatars/wx.jpg",
        phone="13800003333",
    )
    db_session.add(customer)
    await db_session.flush()
    db_session.add(
        CustomerWechatAccount(
            id=13,
            customer_id=customer.id,
            openid="wx_real_003",
            profile_completed=1,
            nickname="微信张三",
            avatar_url="/uploads/avatars/wx.jpg",
        )
    )
    db_session.add(
        CustomerAlipayAccount(
            id=23,
            customer_id=customer.id,
            alipay_user_id="ali_real_003",
            profile_completed=1,
            nickname="支付宝李四",
            avatar_url="/uploads/avatars/ali.jpg",
        )
    )
    await db_session.flush()

    await CustomerService.save_channel_profile(
        db_session, customer.id, "alipay", nickname="支付宝新名", avatar_url="/uploads/avatars/ali2.jpg"
    )
    wx = await CustomerService.resolve_channel_profile(db_session, customer, "wechat")
    ali = await CustomerService.resolve_channel_profile(db_session, customer, "alipay")
    assert wx["nickname"] == "微信张三"
    assert ali["nickname"] == "支付宝新名"
    assert ali["avatar_url"] == "/uploads/avatars/ali2.jpg"
    assert customer.nickname == "微信张三"

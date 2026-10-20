"""员工侧顾客档案 PATCH 与收藏接口入参校验。"""
from datetime import date

import pytest
from pydantic import ValidationError


def test_customer_update_phone_and_patch_semantics():
    from app.api.customers import CustomerUpdate

    with pytest.raises(ValidationError):
        CustomerUpdate(phone="12345")

    # 未传字段不进入 fields_set
    body = CustomerUpdate(display_name="阿面")
    assert "display_name" in body.model_fields_set
    assert "phone" not in body.model_fields_set

    # 显式 null 可清空
    cleared = CustomerUpdate(phone=None, birthday=None)
    assert "phone" in cleared.model_fields_set
    assert cleared.phone is None
    assert cleared.birthday is None

    ok = CustomerUpdate(
        nickname="小面",
        phone="13900001111",
        status=0,
        birthday=date(1990, 5, 1),
        recommend_opt_in=0,
    )
    assert ok.phone == "13900001111"
    assert ok.status == 0
    assert ok.birthday == date(1990, 5, 1)


def test_staff_favorite_routes_registered():
    from app.api.customers import router as customers_router
    from app.api.customers_favorites import router as favorites_router

    customer_paths = {getattr(r, "path", None) for r in customers_router.routes}
    favorite_paths = {getattr(r, "path", None) for r in favorites_router.routes}
    assert "/customers/{customer_id}" in customer_paths
    assert "/customers/{customer_id}/favorites/{product_id}" in favorite_paths
    methods = {
        tuple(sorted(getattr(r, "methods", []) or []))
        for r in customers_router.routes
        if getattr(r, "path", None) == "/customers/{customer_id}"
    }
    assert ("PATCH",) in methods or any("PATCH" in m for m in methods)


@pytest.mark.asyncio
async def test_admin_rename_syncs_channel_nicknames(db_session):
    """后台改展示名/昵称后，同步到微信/支付宝渠道，小程序才能读到新名。"""
    from sqlalchemy import select

    from app.models import Customer, CustomerAlipayAccount, CustomerWechatAccount
    from app.services.customer_service import CustomerService

    customer = Customer(
        id=9101,
        customer_no="CU_ADMIN_RENAME",
        status=1,
        nickname="旧昵称",
        display_name="旧展示名",
    )
    db_session.add(customer)
    await db_session.flush()
    db_session.add(
        CustomerWechatAccount(
            id=91011,
            customer_id=customer.id,
            openid="wx_admin_rename",
            profile_completed=1,
            nickname="旧昵称",
        )
    )
    db_session.add(
        CustomerAlipayAccount(
            id=91012,
            customer_id=customer.id,
            alipay_user_id="ali_admin_rename",
            profile_completed=1,
            nickname="旧昵称",
        )
    )
    await db_session.flush()

    customer.nickname = "新昵称"
    customer.display_name = "新展示名"
    await CustomerService.sync_display_to_channel_accounts(db_session, customer, nickname="新昵称")
    await db_session.flush()

    wx = (
        await db_session.execute(
            select(CustomerWechatAccount).where(CustomerWechatAccount.customer_id == customer.id)
        )
    ).scalar_one()
    ali = (
        await db_session.execute(
            select(CustomerAlipayAccount).where(CustomerAlipayAccount.customer_id == customer.id)
        )
    ).scalar_one()
    assert wx.nickname == "新昵称"
    assert ali.nickname == "新昵称"
    assert (await CustomerService.resolve_channel_profile(db_session, customer, "wechat"))[
        "nickname"
    ] == "新昵称"

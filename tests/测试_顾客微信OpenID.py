"""顾客微信 openid 解析（多账号合并场景）。"""
import pytest

from app.models import Customer, CustomerWechatAccount
from app.services.customer_service import CustomerService


@pytest.mark.asyncio
async def test_resolve_wechat_openid_picks_latest_when_multiple(db_session):
    customer = Customer(id=901, customer_no="CU_TEST_001", status=1)
    db_session.add(customer)
    await db_session.flush()
    db_session.add_all(
        [
            CustomerWechatAccount(id=1, customer_id=customer.id, openid="wx_old_001"),
            CustomerWechatAccount(id=2, customer_id=customer.id, openid="wx_new_002"),
        ]
    )
    await db_session.flush()

    openid = await CustomerService.resolve_wechat_openid(db_session, customer.id)
    assert openid == "wx_new_002"


@pytest.mark.asyncio
async def test_resolve_wechat_openid_prefers_current_session(db_session):
    customer = Customer(id=902, customer_no="CU_TEST_002", status=1)
    db_session.add(customer)
    await db_session.flush()
    db_session.add_all(
        [
            CustomerWechatAccount(id=1, customer_id=customer.id, openid="wx_old_001"),
            CustomerWechatAccount(id=2, customer_id=customer.id, openid="wx_new_002"),
        ]
    )
    await db_session.flush()

    openid = await CustomerService.resolve_wechat_openid(
        db_session, customer.id, prefer_openid="wx_old_001"
    )
    assert openid == "wx_old_001"

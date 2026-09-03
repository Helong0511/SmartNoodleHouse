"""扫码注册门店：只记第一次，换店点餐不覆盖。"""
import pytest

from app.models import Customer, CustomerAlipayAccount, CustomerWechatAccount, DiningTable, Store
from app.services.customer_service import CustomerService


async def _two_stores(db):
    a = Store(id=21, store_no="SH_A", name="总店", status=1)
    b = Store(id=22, store_no="SH_B", name="二分店", status=1)
    db.add_all([a, b])
    await db.flush()
    t1 = DiningTable(id=31, store_id=a.id, table_no="A01", table_name="1号桌", qr_token="tok-store-a", status=1)
    t2 = DiningTable(id=32, store_id=b.id, table_no="B01", table_name="1号桌", qr_token="tok-store-b", status=1)
    db.add_all([t1, t2])
    await db.flush()
    return a, b, t1, t2


@pytest.mark.asyncio
async def test_remember_registered_scan_keeps_first_store(db_session):
    store_a, store_b, table_a, _table_b = await _two_stores(db_session)
    customer = Customer(id=921, customer_no="CU_REG_STORE", status=1)
    db_session.add(customer)
    await db_session.flush()

    await CustomerService.remember_registered_scan(db_session, customer, "tok-store-a")
    assert customer.registered_store_id == store_a.id
    assert customer.registered_table_id == table_a.id

    await CustomerService.remember_registered_scan(db_session, customer, "tok-store-b")
    assert customer.registered_store_id == store_a.id
    assert customer.registered_table_id == table_a.id

    payload = (await CustomerService.registered_scan_map(db_session, [customer]))[customer.id]
    assert payload["registered_store_name"] == "总店"
    assert payload["registered_table_name"] == "1号桌"


@pytest.mark.asyncio
async def test_phone_merge_keeps_target_registered_store(db_session):
    store_a, store_b, table_a, table_b = await _two_stores(db_session)
    wechat = Customer(
        id=922,
        customer_no="CU_REG_WX",
        status=1,
        phone="13900001111",
        registered_store_id=store_a.id,
        registered_table_id=table_a.id,
    )
    alipay = Customer(
        id=923,
        customer_no="CU_REG_ALI",
        status=1,
        registered_store_id=store_b.id,
        registered_table_id=table_b.id,
    )
    db_session.add_all([wechat, alipay])
    await db_session.flush()
    db_session.add(CustomerWechatAccount(id=41, customer_id=wechat.id, openid="wx_reg_1", profile_completed=1))
    db_session.add(CustomerAlipayAccount(id=42, customer_id=alipay.id, alipay_user_id="ali_reg_1", profile_completed=1))
    await db_session.flush()

    merged = await CustomerService.try_link_by_phone(db_session, alipay, "13900001111")
    assert merged.id == wechat.id
    assert merged.registered_store_id == store_a.id
    assert merged.registered_table_id == table_a.id


@pytest.mark.asyncio
async def test_phone_merge_copies_store_when_target_empty(db_session):
    _store_a, store_b, _table_a, table_b = await _two_stores(db_session)
    wechat = Customer(id=924, customer_no="CU_REG_WX2", status=1, phone="13900002222")
    alipay = Customer(
        id=925,
        customer_no="CU_REG_ALI2",
        status=1,
        registered_store_id=store_b.id,
        registered_table_id=table_b.id,
    )
    db_session.add_all([wechat, alipay])
    await db_session.flush()
    db_session.add(CustomerWechatAccount(id=43, customer_id=wechat.id, openid="wx_reg_2", profile_completed=1))
    db_session.add(CustomerAlipayAccount(id=44, customer_id=alipay.id, alipay_user_id="ali_reg_2", profile_completed=1))
    await db_session.flush()

    merged = await CustomerService.try_link_by_phone(db_session, alipay, "13900002222")
    assert merged.id == wechat.id
    assert merged.registered_store_id == store_b.id
    assert merged.registered_table_id == table_b.id

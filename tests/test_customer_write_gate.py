import inspect

import pytest
from sqlalchemy import select

from app.core.exceptions import AppException
from app.models import Customer, CustomerWriteGate
from app.services.customer_write_gate import (
    lock_active_customer_for_write,
    lock_customer_write_gates,
)


@pytest.mark.asyncio
async def test_customer_orm_insert_creates_write_gate_in_same_transaction(db_session):
    customer = Customer(customer_no="GATE-TEST-0001", status=1, account_status="ACTIVE")
    db_session.add(customer)
    await db_session.flush()

    gate_id = await db_session.scalar(
        select(CustomerWriteGate.customer_id).where(
            CustomerWriteGate.customer_id == customer.id
        )
    )
    assert gate_id == customer.id


@pytest.mark.asyncio
async def test_gate_helper_locks_ids_in_sorted_unique_order(db_session):
    customers = [
        Customer(customer_no="GATE-TEST-0002", status=1, account_status="ACTIVE"),
        Customer(customer_no="GATE-TEST-0003", status=1, account_status="ACTIVE"),
    ]
    db_session.add_all(customers)
    await db_session.flush()

    ids = [int(customers[1].id), int(customers[0].id), int(customers[1].id)]
    assert await lock_customer_write_gates(db_session, ids) == sorted(set(ids))


@pytest.mark.asyncio
async def test_missing_gate_fails_closed(db_session):
    customer = Customer(customer_no="GATE-TEST-0004", status=1, account_status="ACTIVE")
    db_session.add(customer)
    await db_session.flush()
    await db_session.delete(
        await db_session.get(CustomerWriteGate, customer.id)
    )
    await db_session.flush()

    with pytest.raises(AppException) as exc:
        await lock_customer_write_gates(db_session, [customer.id])
    assert exc.value.code == 50021


@pytest.mark.asyncio
async def test_lock_active_customer_rejects_disabled_account(db_session):
    customer = Customer(customer_no="GATE-TEST-0005", status=0, account_status="DISABLED")
    db_session.add(customer)
    await db_session.flush()

    with pytest.raises(AppException):
        await lock_active_customer_for_write(db_session, customer.id)



def test_paid_order_points_award_acquires_gate_before_order_and_customer_locks():
    from app.services.customer_service import CustomerService

    source = inspect.getsource(CustomerService.award_points_for_paid_order)
    gate_at = source.index("await lock_customer_write_gates")
    order_lock_at = source.index(".with_for_update()")
    customer_lock_at = source.index("select(Customer)")
    assert gate_at < order_lock_at < customer_lock_at
    assert "populate_existing=True" in source
    assert "observed_customer_id" in source


def test_points_exchange_acquires_gate_before_customer_lock():
    from app.services.customer_service import CustomerService

    source = inspect.getsource(CustomerService.exchange_points_for_coupon)
    gate_at = source.index("await lock_customer_write_gates")
    customer_lock_at = source.index("select(Customer)")
    assert gate_at < customer_lock_at
    assert ".with_for_update()" in source
    assert "populate_existing=True" in source

def test_pos_phone_reuse_acquires_gate_and_current_reads_customer_before_mutation():
    from app.services.customer_service import CustomerService

    source = inspect.getsource(CustomerService.remember_pos_phone)
    gate_at = source.index("await lock_customer_write_gates")
    customer_lock_at = source.index(".with_for_update()")
    mutation_at = source.index("existing.registered_store_id =")
    assert gate_at < customer_lock_at < mutation_at
    assert "populate_existing=True" in source
    assert "observed_customer_id" in source
    assert "locked.phone" in source

def test_miniprogram_login_finalization_acquires_customer_gate_before_profile_writes():
    from app.api.miniprogram_auth import _finalize_channel_login

    source = inspect.getsource(_finalize_channel_login)
    gate_at = source.index("await lock_active_customer_for_write")
    profile_at = source.index("CustomerService.save_channel_profile")
    first_seen_at = source.index("CustomerService.remember_first_seen")
    assert gate_at < profile_at < first_seen_at
    assert "customer = await lock_active_customer_for_write" in source


def test_miniprogram_logout_acquires_customer_gate_before_token_version_write():
    from app.api.miniprogram_auth import customer_logout

    source = inspect.getsource(customer_logout)
    gate_at = source.index("await lock_active_customer_for_write")
    mutation_at = source.index("customer.token_version =")
    assert gate_at < mutation_at

def test_verified_phone_binding_acquires_all_gates_before_identity_lock():
    from app.services.customer_service import CustomerService

    source = inspect.getsource(CustomerService.bind_verified_phone)
    gate_at = source.index("await lock_customer_write_gates")
    identity_lock_at = source.index("identity_stmt.with_for_update()")
    merge_at = source.index("CustomerMergeService.merge")
    assert gate_at < identity_lock_at < merge_at
    assert "observed_peer_ids" in source
    assert "current_peer_ids != expected_peer_ids" in source


def test_unverified_phone_save_refreshes_customer_after_lifecycle_gate():
    from app.services.customer_service import CustomerService

    source = inspect.getsource(CustomerService.save_unverified_phone)
    gate_at = source.index("await lock_active_customer_for_write")
    mutation_at = source.index("customer.phone = phone")
    assert gate_at < mutation_at


def test_miniprogram_member_profile_and_avatar_writes_acquire_lifecycle_gate():
    from app.api.miniprogram_member import (
        import_member_avatar_from_url,
        patch_member_profile,
        upload_member_avatar,
    )

    for endpoint in (patch_member_profile, upload_member_avatar, import_member_avatar_from_url):
        source = inspect.getsource(endpoint)
        gate_at = source.index("await lock_active_customer_for_write")
        assert gate_at >= 0

def test_existing_wechat_identity_is_gated_before_channel_account_repair():
    from app.services.customer_service import CustomerService

    source = inspect.getsource(CustomerService.get_or_create_by_wechat)
    gate_at = source.index("lock_active_customer_for_write")
    ensure_at = source.index("CustomerService._ensure_wechat_account")
    assert gate_at < ensure_at


def test_existing_alipay_identity_is_gated_before_identity_claim_or_account_insert():
    from app.services.customer_service import CustomerService

    source = inspect.getsource(CustomerService.get_or_create_by_alipay)
    gate_at = source.index("lock_active_customer_for_write")
    claim_at = source.index("CustomerService._claim_identity")
    account_insert_at = source.index("db.add(")
    assert gate_at < min(claim_at, account_insert_at)


from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Customer, CustomerBehaviorEvent
from app.services import behavior_event_service
from app.services.audit_service import AuditService
from app.services.privacy_service import PrivacyService
from app.services import reco_cache
from app.services.taste_learning_service import TasteLearningService
from app.services.preference_service import PreferenceService
from reco_factories import make_customer


@pytest.mark.asyncio
async def test_customer_consent_lock_uses_for_update_and_refreshes_identity_map():
    class Result:
        def scalar_one_or_none(self):
            return customer

    class Session:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return Result()

    customer = Customer(id=91, customer_no="LOCK-91", recommend_opt_in=0)
    session = Session()

    locked = await PrivacyService.lock_customer_for_recommendation(session, 91)

    assert locked is customer
    assert session.statement._for_update_arg is not None
    assert session.statement.get_execution_options()["populate_existing"] is True


@pytest.mark.asyncio
async def test_event_ingestion_uses_locked_consent_state(monkeypatch):
    customer = Customer(id=92, customer_no="LOCK-92", recommend_opt_in=1)
    locked_customer = Customer(id=92, customer_no="LOCK-92", recommend_opt_in=0)
    calls = []

    async def locked(_db, customer_id):
        calls.append(customer_id)
        return locked_customer

    monkeypatch.setattr(PrivacyService, "lock_customer_for_recommendation", locked)

    result = await behavior_event_service.ingest_events(
        object(), customer, [{"event_id": "must-not-store", "event_type": "CLICK"}]
    )

    assert calls == [92]
    assert result == {
        "accepted": 0,
        "duplicate": 0,
        "dropped": 1,
        "reason": "opt_out",
    }


@pytest.mark.asyncio
async def test_profile_learning_uses_locked_consent_state(monkeypatch):
    locked_customer = Customer(id=93, customer_no="LOCK-93", recommend_opt_in=0)
    calls = []

    async def locked(_db, customer_id):
        calls.append(customer_id)
        return locked_customer

    monkeypatch.setattr(PrivacyService, "lock_customer_for_recommendation", locked)

    assert await TasteLearningService.learn_from_events(object(), 93) == 0
    assert calls == [93]


@pytest.mark.asyncio
@pytest.mark.mysql
@pytest.mark.parametrize("revoke_first", [False, True])
async def test_mysql_consent_revocation_and_event_ingestion_are_serialized(
    db_session, monkeypatch, revoke_first
):
    if db_session.bind.dialect.name != "mysql":
        pytest.skip("SELECT FOR UPDATE concurrency requires the MySQL CI database")

    customer = await make_customer(db_session, recommend_opt_in=1)
    customer_id = int(customer.id)
    await db_session.commit()

    async_session = async_sessionmaker(db_session.bind, expire_on_commit=False)
    lock_acquired = asyncio.Event()
    continue_first = asyncio.Event()
    second_started = asyncio.Event()

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(AuditService, "log", staticmethod(no_audit))
    monkeypatch.setattr(reco_cache, "spawn_bg", lambda coro: coro.close())

    async def ingest():
        async with async_session() as session:
            current = await session.get(Customer, customer_id)
            if revoke_first:
                second_started.set()
                await lock_acquired.wait()
            result = await behavior_event_service.ingest_events(
                session,
                current,
                [{"event_id": "consent-race-event", "event_type": "CLICK"}],
            )
            if not revoke_first:
                lock_acquired.set()
                await continue_first.wait()
            await session.commit()
            return result

    async def revoke():
        async with async_session() as session:
            current = await session.get(Customer, customer_id)
            if revoke_first:
                await PrivacyService.lock_customer_for_recommendation(session, customer_id)
                await PrivacyService.update_opt_in(session, current, 0)
                lock_acquired.set()
                await continue_first.wait()
                await session.commit()
            else:
                await lock_acquired.wait()
                second_started.set()
                await PrivacyService.update_opt_in(session, current, 0)
                await session.commit()

    if revoke_first:
        first = asyncio.create_task(revoke())
        await asyncio.wait_for(lock_acquired.wait(), timeout=5)
        second = asyncio.create_task(ingest())
        await asyncio.wait_for(second_started.wait(), timeout=5)
        continue_first.set()
        await asyncio.wait_for(first, timeout=10)
        second_result = await asyncio.wait_for(second, timeout=10)
        assert second_result["accepted"] == 0
    else:
        first = asyncio.create_task(ingest())
        await asyncio.wait_for(lock_acquired.wait(), timeout=5)
        second = asyncio.create_task(revoke())
        await asyncio.wait_for(second_started.wait(), timeout=5)
        continue_first.set()
        first_result = await asyncio.wait_for(first, timeout=10)
        await asyncio.wait_for(second, timeout=10)
        assert first_result["accepted"] == 1

    async with async_session() as verify:
        stored_customer = await verify.get(Customer, customer_id)
        events = list(
            (
                await verify.execute(
                    select(CustomerBehaviorEvent).where(
                        CustomerBehaviorEvent.customer_id == customer_id
                    )
                )
            ).scalars().all()
        )
        assert stored_customer.recommend_opt_in == 0
        assert events == []



@pytest.mark.asyncio
async def test_preference_recompute_uses_locked_consent_state(db_session, monkeypatch):
    customer = await make_customer(db_session, recommend_opt_in=1)
    customer_id = int(customer.id)
    locked_customer = Customer(
        id=customer_id, customer_no=customer.customer_no, recommend_opt_in=0
    )
    calls = []

    async def locked(_db, requested_id):
        calls.append(requested_id)
        return locked_customer

    monkeypatch.setattr(PrivacyService, "lock_customer_for_recommendation", locked)

    await PreferenceService.recompute_for_customer(db_session, customer_id)

    assert calls == [customer_id]
    from app.models import CustomerPreference

    remaining = list(
        (
            await db_session.execute(
                select(CustomerPreference).where(
                    CustomerPreference.customer_id == customer_id
                )
            )
        ).scalars().all()
    )
    assert remaining == []


@pytest.mark.asyncio
async def test_privacy_settings_reads_locked_consent_state(monkeypatch):
    stale_customer = Customer(id=94, customer_no="LOCK-94", recommend_opt_in=1)
    latest_customer = Customer(
        id=94,
        customer_no="LOCK-94",
        recommend_opt_in=0,
        consent_version="1.0",
        recommend_consent_at=None,
    )
    calls = []

    async def locked(_db, customer_id):
        calls.append(customer_id)
        return latest_customer

    monkeypatch.setattr(PrivacyService, "lock_customer_for_recommendation", locked)

    result = await PrivacyService.get_privacy_settings(object(), stale_customer)

    assert calls == [94]
    assert result["recommend_opt_in"] == 0
    assert result["consent_version"] == "1.0"

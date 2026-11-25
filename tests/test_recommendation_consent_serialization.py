from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Customer, CustomerBehaviorEvent, CustomerPreference, CustomerTasteProfile, Order
from app.services import behavior_event_service
from app.services.audit_service import AuditService
from app.services.privacy_service import PrivacyService
from app.services import reco_cache
from app.services.taste_learning_service import TasteLearningService
from app.services.preference_service import PreferenceService
from app.services.feedback_service import FeedbackService
from app.services.customer_merge_service import CustomerMergeService, _lock_merge_scope
from reco_factories import make_customer
from app.api import miniprogram_menu


@pytest.mark.asyncio
async def test_customer_consent_lock_uses_for_update_and_refreshes_identity_map(monkeypatch):
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
    from app.services import privacy_service

    async def no_gate(_db, _ids):
        return [91]

    monkeypatch.setattr(privacy_service, "lock_customer_write_gates", no_gate)

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


async def test_profile_learning_rejects_order_owner_changed_while_waiting_for_gate(monkeypatch):
    stale_order = Order(id=940, customer_id=93, payment_status="PAID")
    current_order = Order(id=940, customer_id=94, payment_status="PAID")
    statements = []

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class Session:
        async def get(self, model, order_id):
            assert model is Order
            assert order_id == 940
            return stale_order

        async def execute(self, statement):
            statements.append(statement)
            entity = statement.column_descriptions[0]["entity"]
            if entity is Order:
                return Result(current_order)
            if entity is Customer:
                return Result(Customer(id=93, customer_no="LOCK-93", recommend_opt_in=1))
            raise AssertionError(f"unexpected locked entity: {entity}")

    gate_calls = []

    async def lock_gates(_db, customer_ids):
        gate_calls.append(list(customer_ids))

    from app.services import customer_write_gate

    monkeypatch.setattr(customer_write_gate, "lock_customer_write_gates", lock_gates)
    session = Session()

    with pytest.raises(RuntimeError, match="order customer changed"):
        await TasteLearningService._lock_order_customer_for_learning(session, 940)

    assert gate_calls == [[93]]
    assert len(statements) == 1
    assert statements[0]._for_update_arg is not None
    assert statements[0].get_execution_options()["populate_existing"] is True


@pytest.mark.asyncio
async def test_profile_learning_lock_order_is_gate_order_customer(monkeypatch):
    order = Order(id=941, customer_id=95, payment_status="PAID")
    customer = Customer(id=95, customer_no="LOCK-95", recommend_opt_in=1)
    entities = []
    gate_calls = []

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class Session:
        async def get(self, model, order_id):
            assert model is Order
            assert order_id == 941
            return order

        async def execute(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            entities.append(entity)
            return Result(order if entity is Order else customer)

    async def lock_gates(_db, customer_ids):
        gate_calls.append(list(customer_ids))

    from app.services import customer_write_gate

    monkeypatch.setattr(customer_write_gate, "lock_customer_write_gates", lock_gates)
    locked_order, locked_customer = await TasteLearningService._lock_order_customer_for_learning(
        Session(), 941
    )

    assert locked_order is order
    assert locked_customer is customer
    assert gate_calls == [[95]]
    assert entities == [Order, Customer]


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


@pytest.mark.asyncio
@pytest.mark.mysql
async def test_mysql_preference_projection_uses_current_read_after_revocation(
    db_session, monkeypatch
):
    if db_session.bind.dialect.name != "mysql":
        pytest.skip("locking current reads require the MySQL CI database")

    customer = await make_customer(db_session, recommend_opt_in=1)
    customer_id = int(customer.id)
    db_session.add(
        CustomerPreference(
            customer_id=customer_id,
            category="辣度",
            option_code="MILD",
            option_name="微辣",
            preference_score="0.5000",
            order_count=2,
        )
    )
    await db_session.commit()

    async_session = async_sessionmaker(db_session.bind, expire_on_commit=False)
    lock_acquired = asyncio.Event()
    snapshot_read = asyncio.Event()

    async def no_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(AuditService, "log", staticmethod(no_audit))
    monkeypatch.setattr(reco_cache, "spawn_bg", lambda coro: coro.close())

    async def revoke():
        async with async_session() as session:
            current = await session.get(Customer, customer_id)
            await PrivacyService.lock_customer_for_recommendation(session, customer_id)
            lock_acquired.set()
            await asyncio.wait_for(snapshot_read.wait(), timeout=5)
            await PrivacyService.update_opt_in(session, current, 0)
            await session.commit()

    async def read_preferences():
        await asyncio.wait_for(lock_acquired.wait(), timeout=5)
        async with async_session() as session:
            # Establish a REPEATABLE READ snapshot before revocation commits.
            old_rows = list(
                (
                    await session.execute(
                        select(CustomerPreference).where(
                            CustomerPreference.customer_id == customer_id
                        )
                    )
                ).scalars().all()
            )
            assert len(old_rows) == 1
            snapshot_read.set()

            current = await PrivacyService.lock_customer_for_recommendation(
                session, customer_id
            )
            assert current is not None
            assert current.recommend_opt_in == 0
            # This mirrors the API projection: FOR UPDATE is a current read,
            # not a stale consistent read from the earlier transaction snapshot.
            rows = list(
                (
                    await session.execute(
                        select(CustomerPreference)
                        .where(CustomerPreference.customer_id == customer_id)
                        .with_for_update()
                    )
                ).scalars().all()
            )
            await session.commit()
            return rows

    revoke_task = asyncio.create_task(revoke())
    await asyncio.wait_for(lock_acquired.wait(), timeout=5)
    rows = await asyncio.wait_for(read_preferences(), timeout=10)
    await asyncio.wait_for(revoke_task, timeout=10)
    assert rows == []


@pytest.mark.asyncio
@pytest.mark.mysql
async def test_mysql_event_ingestion_uses_current_read_for_idempotency(
    db_session, monkeypatch
):
    if db_session.bind.dialect.name != "mysql":
        pytest.skip("current-read idempotency regression requires MySQL REPEATABLE READ")

    customer = await make_customer(db_session, recommend_opt_in=1)
    customer_id = int(customer.id)
    await db_session.commit()

    async_session = async_sessionmaker(db_session.bind, expire_on_commit=False)
    snapshot_ready = asyncio.Event()
    first_insert_committed = asyncio.Event()

    async def stale_snapshot_request():
        async with async_session() as session:
            # Establish a REPEATABLE READ snapshot before the competing insert.
            await session.execute(
                select(CustomerBehaviorEvent.id).where(
                    CustomerBehaviorEvent.customer_id == customer_id
                )
            )
            snapshot_ready.set()
            await asyncio.wait_for(first_insert_committed.wait(), timeout=10)
            result = await behavior_event_service.ingest_events(
                session,
                await session.get(Customer, customer_id),
                [{"event_id": "same-event-id", "event_type": "CLICK"}],
            )
            await session.commit()
            return result

    async def first_insert():
        await asyncio.wait_for(snapshot_ready.wait(), timeout=10)
        async with async_session() as session:
            result = await behavior_event_service.ingest_events(
                session,
                await session.get(Customer, customer_id),
                [{"event_id": "same-event-id", "event_type": "CLICK"}],
            )
            assert result["accepted"] == 1
            await session.commit()
            first_insert_committed.set()

    stale_task = asyncio.create_task(stale_snapshot_request())
    insert_task = asyncio.create_task(first_insert())
    await asyncio.wait_for(insert_task, timeout=15)
    stale_result = await asyncio.wait_for(stale_task, timeout=15)

    assert stale_result["accepted"] == 0
    assert stale_result["duplicate"] == 1
    async with async_session() as verify:
        count = await verify.scalar(
            select(func.count()).select_from(CustomerBehaviorEvent).where(
                CustomerBehaviorEvent.customer_id == customer_id,
                CustomerBehaviorEvent.event_id == "same-event-id",
            )
        )
        assert count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_opt_in", "target_opt_in"),
    [(0, 1), (1, 0), (0, 0)],
)
async def test_customer_merge_does_not_resurrect_preferences_without_both_consents(
    db_session, source_opt_in, target_opt_in
):
    source = await make_customer(db_session, recommend_opt_in=source_opt_in)
    target = await make_customer(db_session, recommend_opt_in=target_opt_in)
    db_session.add_all(
        [
            CustomerPreference(
                customer_id=int(source.id),
                category="辣度",
                option_code="HOT",
                option_name="特辣",
                preference_score="0.9000",
                order_count=3,
            ),
            CustomerPreference(
                customer_id=int(target.id),
                category="辣度",
                option_code="MILD",
                option_name="微辣",
                preference_score="0.7000",
                order_count=2,
            ),
        ]
    )
    await db_session.flush()

    stats = {"preferences_moved": 0, "preferences_merged": 0}
    await CustomerMergeService._merge_preferences_for_consent(
        db_session, source, target, stats
    )

    remaining = list(
        (
            await db_session.execute(
                select(CustomerPreference).where(
                    CustomerPreference.customer_id.in_((int(source.id), int(target.id)))
                )
            )
        ).scalars().all()
    )
    assert remaining == []
    assert stats["preferences_cleared_for_consent"] == 2


@pytest.mark.asyncio
async def test_customer_merge_locks_orders_and_recharges_before_customers():
    class ScalarResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class RecordingSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return ScalarResult()

    session = RecordingSession()
    await _lock_merge_scope(session, [11, 22])

    sql = [
        str(statement.compile(dialect=mysql.dialect())).lower()
        for statement in session.statements
    ]
    assert len(sql) == 4
    assert "from orders" in sql[0] and "for update" in sql[0]
    assert "order by orders.id" in sql[0]
    assert "from recharge_orders" in sql[1] and "for update" in sql[1]
    assert "order by recharge_orders.id" in sql[1]
    assert "from marketing_flash_sale_claims" in sql[2] and "for update" in sql[2]
    assert "order by marketing_flash_sale_claims.id" in sql[2]
    assert "from customers" in sql[3] and "for update" in sql[3]
    assert "order by customers.id" in sql[3]

@pytest.mark.asyncio
async def test_preference_update_uses_current_locked_order_owner(monkeypatch):
    current_order = Order(id=950, customer_id=94, status="PAID")
    current_customer = Customer(id=94, customer_no="LOCK-94", recommend_opt_in=1)
    calls = []

    async def locked(_db, order_id):
        calls.append(("lock", order_id))
        return current_order, current_customer

    async def recompute(_db, customer_id, *, locked_customer=None):
        assert locked_customer is current_customer
        calls.append(("recompute", customer_id))

    async def promote(_db, customer_id):
        calls.append(("promote", customer_id))

    monkeypatch.setattr(
        TasteLearningService,
        "_lock_order_customer_for_learning",
        staticmethod(locked),
    )
    monkeypatch.setattr(
        PreferenceService, "recompute_for_customer", staticmethod(recompute)
    )
    monkeypatch.setitem(
        sys.modules,
        "app.services.face_service",
        SimpleNamespace(face_embedding_cache=SimpleNamespace(promote=promote)),
    )

    await PreferenceService.update_from_order(object(), 950)

    assert [call[:2] for call in calls] == [
        ("lock", 950),
        ("recompute", 94),
        ("promote", 94),
    ]
    assert current_customer.last_order_at is not None


@pytest.mark.asyncio
async def test_feedback_submit_aborts_when_order_owner_changes_while_locking(monkeypatch):
    calls = []

    async def changed(_db, order_id):
        calls.append(order_id)
        raise RuntimeError("order customer changed while acquiring recommendation gate; retry outbox")

    monkeypatch.setattr(
        TasteLearningService,
        "_lock_order_customer_for_learning",
        staticmethod(changed),
    )

    with pytest.raises(RuntimeError, match="order customer changed"):
        await FeedbackService.submit_order_feedback(
            object(),
            Order(id=951, customer_id=93, status="PAID"),
            [],
        )

    assert calls == [951]




@pytest.mark.asyncio
async def test_opted_out_recommendation_never_reads_customer_scoped_cache(monkeypatch):
    stale_customer = Customer(id=960, customer_no="LOCK-960", recommend_opt_in=1)
    locked_customer = Customer(
        id=960, customer_no="LOCK-960", recommend_opt_in=0,
        status=1, account_status="ACTIVE",
    )
    gate_calls = []
    recommend_calls = []

    async def lock_current(_db, customer_id):
        gate_calls.append(customer_id)
        return locked_customer

    async def recommend(
        _db, store_id, *, customer_id=None, scene="recommend_menu",
        limit=10, exclude_ids=None, use_cache=True, weight_override=None,
    ):
        recommend_calls.append((customer_id, scene, limit, weight_override))
        return [], "COLD", {"pool_size": 0}

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("opted-out recommendation must not access customer cache")

    monkeypatch.setattr(
        PrivacyService, "lock_customer_for_recommendation", staticmethod(lock_current)
    )
    monkeypatch.setattr(
        miniprogram_menu.RecommendationService, "recommend", staticmethod(recommend)
    )
    monkeypatch.setattr(miniprogram_menu, "get_reco_generation", forbidden)
    monkeypatch.setattr(miniprogram_menu, "get_cached_reco", forbidden)
    monkeypatch.setattr(miniprogram_menu.AbService, "assign", staticmethod(forbidden))

    response = await miniprogram_menu.get_recommendations(
        request=None,
        db=object(),
        customer=stale_customer,
        store_id=1,
    )

    assert gate_calls == [960]
    assert recommend_calls == [(960, "recommend_menu", 10, None)]
    assert response["data"]["state"] == "COLD"
    assert response["data"]["cache_hit"] is False



@pytest.mark.asyncio
async def test_customer_recommendation_cache_hit_is_checked_only_after_consent_gate(
    monkeypatch,
):
    customer = Customer(
        id=961, customer_no="LOCK-961", recommend_opt_in=1,
        status=1, account_status="ACTIVE",
    )
    calls = []

    async def lock_current(_db, customer_id):
        calls.append(("gate", customer_id))
        return customer

    async def assign(_db, customer_id, experiment_key):
        calls.append(("ab", customer_id))
        return "v1", {}, None

    async def epoch(_db, customer_id):
        calls.append(("epoch", customer_id))
        return 123

    async def generation(customer_id):
        calls.append(("generation", customer_id))
        return 7

    async def cached(customer_id, store_id, scene, bucket, fingerprint, *, generation):
        calls.append(("cache", customer_id, generation, fingerprint))
        return {"state": "HOT", "items": [], "meta": {}, "ab": {}}

    monkeypatch.setattr(
        PrivacyService, "lock_customer_for_recommendation", staticmethod(lock_current)
    )
    monkeypatch.setattr(miniprogram_menu.AbService, "assign", staticmethod(assign))
    monkeypatch.setattr(miniprogram_menu, "_latest_reco_cache_epoch", epoch)
    monkeypatch.setattr(miniprogram_menu, "get_reco_generation", generation)
    monkeypatch.setattr(miniprogram_menu, "get_cached_reco", cached)

    response = await miniprogram_menu.get_recommendations(
        request=None,
        db=object(),
        customer=customer,
        store_id=1,
    )

    assert response["data"]["state"] == "HOT"
    assert [item[0] for item in calls] == [
        "gate", "epoch", "ab", "generation", "cache"
    ]
    assert calls[-1][:3] == ("cache", 961, 7)
    assert "epoch=123" in calls[-1][3]



@pytest.mark.asyncio
async def test_reconsent_schedules_cache_invalidation_after_delayed_opt_out(monkeypatch):
    customer = Customer(
        id=962, customer_no="LOCK-962", recommend_opt_in=0,
        status=1, account_status="ACTIVE",
    )
    calls = []

    class Session:
        async def flush(self):
            return None

    session = Session()

    async def lock_current(_db, customer_id):
        assert customer_id == 962
        return customer

    async def audit(*_args, **_kwargs):
        return None

    async def privacy_settings(_db, _customer):
        return {"recommend_opt_in": 1}

    def defer(db, customer_id):
        calls.append((db, customer_id))

    monkeypatch.setattr(
        PrivacyService, "lock_customer_for_recommendation", staticmethod(lock_current)
    )
    monkeypatch.setattr(PrivacyService, "get_privacy_settings", staticmethod(privacy_settings))
    monkeypatch.setattr(AuditService, "log", staticmethod(audit))
    monkeypatch.setattr(reco_cache, "defer_invalidate_customer", defer)

    await PrivacyService.update_opt_in(session, customer, 1)

    assert calls == [(session, 962)]


@pytest.mark.asyncio
async def test_record_consent_schedules_cache_invalidation_when_reenabling(monkeypatch):
    customer = Customer(
        id=963, customer_no="LOCK-963", recommend_opt_in=0,
        status=1, account_status="ACTIVE",
    )
    calls = []

    class Session:
        async def flush(self):
            return None

    session = Session()

    async def lock_current(_db, customer_id):
        assert customer_id == 963
        return customer

    async def audit(*_args, **_kwargs):
        return None

    def defer(db, customer_id):
        calls.append((db, customer_id))

    monkeypatch.setattr(
        PrivacyService, "lock_customer_for_recommendation", staticmethod(lock_current)
    )
    monkeypatch.setattr(AuditService, "log", staticmethod(audit))
    monkeypatch.setattr(reco_cache, "defer_invalidate_customer", defer)

    result = await PrivacyService.record_consent(session, customer)

    assert result["recommend_opt_in"] == 1
    assert calls == [(session, 963)]



@pytest.mark.asyncio
async def test_recommendation_cache_epoch_uses_current_locked_outbox_read():
    class Result:
        def scalar_one_or_none(self):
            return 321

    class Session:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return Result()

    session = Session()
    epoch = await miniprogram_menu._latest_reco_cache_epoch(session, 964)

    sql = str(session.statement.compile(dialect=mysql.dialect())).lower()
    assert epoch == 321
    assert "from outbox_events" in sql
    assert "aggregate_type" in sql and "aggregate_id" in sql
    assert "order by outbox_events.id desc" in sql
    assert "limit" in sql and "for update" in sql



@pytest.mark.asyncio
async def test_recommendation_profile_read_uses_current_locking_read():
    class Rows:
        def scalars(self):
            return self

        def all(self):
            return []

    class Session:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return Rows()

    session = Session()
    profiles, total_events = await miniprogram_menu.RecommendationService.load_profiles(
        session, 965
    )

    sql = str(
        session.statement.compile(
            dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()
    assert profiles == {}
    assert total_events == 0
    assert "from customer_taste_profiles" in sql
    assert "customer_id = 965" in sql
    assert "for update" in sql



@pytest.mark.asyncio
@pytest.mark.mysql
async def test_mysql_recommendation_profile_read_uses_current_view_after_reconsent(db_session):
    if db_session.bind.dialect.name != "mysql":
        pytest.skip("REPEATABLE READ current-read regression requires MySQL")

    customer = await make_customer(db_session, recommend_opt_in=1)
    customer_id = int(customer.id)
    db_session.add(
        CustomerTasteProfile(
            customer_id=customer_id,
            dim_type="TASTE_TAG",
            target_key="stale-profile",
            target_name="stale-profile",
            score=0.9,
            raw_weight=1.0,
            event_count=3,
        )
    )
    await db_session.commit()

    async_session = async_sessionmaker(db_session.bind, expire_on_commit=False)
    stale_reader = async_session()
    revoker = async_session()
    reconsenter = async_session()
    try:
        # Establish a REPEATABLE READ snapshot before the consent changes.
        stale_customer = await stale_reader.get(Customer, customer_id)
        assert stale_customer is not None

        revoked_customer = await PrivacyService.lock_customer_for_recommendation(
            revoker, customer_id
        )
        assert revoked_customer is not None
        revoked_customer.recommend_opt_in = 0
        from sqlalchemy import delete

        await revoker.execute(
            delete(CustomerTasteProfile).where(
                CustomerTasteProfile.customer_id == customer_id
            )
        )
        await revoker.commit()

        reconsented_customer = await PrivacyService.lock_customer_for_recommendation(
            reconsenter, customer_id
        )
        assert reconsented_customer is not None
        reconsented_customer.recommend_opt_in = 1
        await reconsenter.commit()

        # The locked Customer current read sees re-consent, while the profile
        # loader must also use a current read rather than the stale snapshot.
        locked_customer = await PrivacyService.lock_customer_for_recommendation(
            stale_reader, customer_id
        )
        assert locked_customer is not None
        assert int(locked_customer.recommend_opt_in or 0) == 1

        profiles, total_events = await miniprogram_menu.RecommendationService.load_profiles(
            stale_reader, customer_id
        )
        assert profiles == {}
        assert total_events == 0
    finally:
        await stale_reader.close()
        await revoker.close()
        await reconsenter.close()


@pytest.mark.asyncio
async def test_ab_assignment_integrity_race_keeps_outer_customer_gate_transaction():
    from sqlalchemy.exc import IntegrityError

    from app.models import AbAssignment, AbExperiment
    from app.services.ab_service import AbService

    experiment = AbExperiment(
        experiment_key="reco_algo",
        status="RUNNING",
        traffic_percent=100,
        default_bucket="v1",
        buckets=[
            {"code": "v1", "traffic": 50, "params": {}},
            {"code": "v2", "traffic": 50, "params": {}},
        ],
    )
    existing = AbAssignment(
        experiment_key="reco_algo", customer_id=987654, bucket="v2"
    )

    class Savepoint:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            # Roll back the savepoint but propagate IntegrityError to the service handler.
            return False

    class Session:
        transaction_active = True
        flush_calls = 0
        scalar_calls = 0
        added = None

        async def scalar(self, statement):
            self.scalar_calls += 1
            if self.scalar_calls == 1:
                return experiment
            if self.flush_calls == 0:
                return None
            return existing

        def begin_nested(self):
            return Savepoint()

        def add(self, value):
            self.added = value

        async def flush(self):
            self.flush_calls += 1
            raise IntegrityError("INSERT", {}, Exception("duplicate assignment"))

        async def rollback(self):
            self.transaction_active = False
            raise AssertionError("A/B race must not roll back the outer transaction")

    session = Session()
    bucket, params, returned_experiment = await AbService.assign(
        session, 987654, "reco_algo"
    )

    assert bucket == "v2"
    assert params == {}
    assert returned_experiment is experiment
    assert session.transaction_active is True
    assert isinstance(session.added, AbAssignment)


@pytest.mark.asyncio
async def test_feedback_learning_outbox_rechecks_current_consent_before_folding(monkeypatch):
    from types import SimpleNamespace
    from app.services.feedback_service import _handle_feedback_taste_learning

    feedback = SimpleNamespace(
        id=501, order_id=601, customer_id=701, product_id=801,
        sentiment="POSITIVE", created_at=None,
    )
    order = SimpleNamespace(id=601, customer_id=701)
    opted_out_customer = SimpleNamespace(id=701, recommend_opt_in=0)

    class Result:
        def scalar_one_or_none(self):
            return feedback

    class Session:
        async def execute(self, _statement):
            return Result()

    async def locked_order_customer(_db, order_id):
        assert order_id == 601
        return order, opted_out_customer

    async def must_not_fold(*_args, **_kwargs):
        raise AssertionError("opted-out feedback must not be folded")

    monkeypatch.setattr(
        TasteLearningService, "_lock_order_customer_for_learning",
        locked_order_customer,
    )
    monkeypatch.setattr(TasteLearningService, "learn_from_feedback", must_not_fold)

    await _handle_feedback_taste_learning(
        Session(),
        SimpleNamespace(aggregate_id=501),
    )

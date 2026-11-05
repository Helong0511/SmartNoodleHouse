"""定时任务调度：跨进程覆盖通知、任务锁、Leader fence、基础设施容错。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_notify_overrides_changed_increments_and_publishes():
    from app.services import scheduler_admin as sa

    fake = MagicMock()
    fake.incr = AsyncMock(return_value=7)
    fake.expire = AsyncMock(return_value=1)
    fake.publish = AsyncMock(return_value=1)

    with patch("app.core.redis_client.get_redis", AsyncMock(return_value=fake)):
        ver = await sa.notify_overrides_changed(job_id="drain_outbox_events")
    assert ver == 7
    fake.incr.assert_awaited()
    fake.publish.assert_awaited()
    args = fake.publish.await_args.args
    assert args[0] == sa.OVERRIDES_CHANNEL
    assert "drain_outbox_events" in args[1]


@pytest.mark.asyncio
async def test_redis_job_lock_exclusive():
    from app.services.scheduler_admin import redis_job_lock

    store: dict[str, str] = {}

    async def _set(key, value, nx=False, ex=None):
        if nx and key in store:
            return False
        store[key] = value
        return True

    async def _eval(script, nkeys, key, token):
        if store.get(key) == token:
            del store[key]
            return 1
        return 0

    fake = MagicMock()
    fake.set = AsyncMock(side_effect=_set)
    fake.eval = AsyncMock(side_effect=_eval)

    with patch("app.core.redis_client.get_redis", AsyncMock(return_value=fake)):
        async with redis_job_lock("t1", ttl=30) as ok1:
            assert ok1 is True
            async with redis_job_lock("t1", ttl=30) as ok2:
                assert ok2 is False
        async with redis_job_lock("t1", ttl=30) as ok3:
            assert ok3 is True


@pytest.mark.asyncio
async def test_redis_job_lock_fails_closed_on_redis_error():
    from app.services.scheduler_admin import redis_job_lock

    with patch("app.core.redis_client.get_redis", AsyncMock(side_effect=RuntimeError("down"))):
        async with redis_job_lock("t2") as ok:
            assert ok is False


@pytest.mark.asyncio
async def test_exclusive_scheduler_job_raises_when_busy():
    from app.services.scheduler_admin import JobSkipped, exclusive_scheduler_job, redis_job_lock

    store: dict[str, str] = {}

    async def _set(key, value, nx=False, ex=None):
        if nx and key in store:
            return False
        store[key] = value
        return True

    async def _eval(script, nkeys, key, token):
        if store.get(key) == token:
            del store[key]
            return 1
        return 0

    fake = MagicMock()
    fake.set = AsyncMock(side_effect=_set)
    fake.eval = AsyncMock(side_effect=_eval)

    with patch("app.core.redis_client.get_redis", AsyncMock(return_value=fake)):
        async with redis_job_lock("busy_job", ttl=30) as ok:
            assert ok is True
            with pytest.raises(JobSkipped, match="job_lock_busy"):
                async with exclusive_scheduler_job("busy_job", ttl=30):
                    pass


def test_task_catalog_contains_marketing_reclaim():
    from app.tasks.scheduler import TASK_CATALOG

    ids = {m["id"] for m in TASK_CATALOG}
    assert "reclaim_stale_marketing_tasks" in ids
    assert "marketing_task_worker" in ids


def test_non_claim_jobs_use_exclusive_scheduler_job():
    """无行级 claim 的副作用任务须走 exclusive_scheduler_job（防双 Leader 双跑）。"""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "backend" / "app" / "tasks" / "scheduler.py"
    text = src.read_text(encoding="utf-8")
    required = [
        "refresh_platform_tokens",
        "auto_open_close_delivery_stores",
        "delivery_stock_reconcile",
        "maintain_file_logs",
        "night_data_batch",
        "sleep_member_wake",
        "balance_low_remind",
        "points_expire",
        "marketing_task_worker",
        "social_love_day_issue",
        "cleanup_visitor_data",
        "cleanup_operation_logs",
        "cleanup_delivery_pii",
        "cleanup_expired_behavior_events",
        "reencrypt_face_profiles",
        "learn_taste_profiles",
    ]
    for job_id in required:
        assert f'exclusive_scheduler_job("{job_id}"' in text, job_id


@pytest.mark.asyncio
async def test_confirm_leader_and_job_enabled_gate():
    from app.core import leader_lock
    from app.services.scheduler_admin import is_job_enabled, set_enabled_cache

    set_enabled_cache({"drain_outbox_events": {"enabled": False}})
    assert is_job_enabled("drain_outbox_events") is False
    assert is_job_enabled("unknown_job") is True

    leader_lock.set_leader_held(True)
    leader_lock.set_leader_worker_id("w-a")
    fake = MagicMock()
    fake.get = AsyncMock(return_value=b"w-a")
    with patch("app.core.leader_lock.get_redis", AsyncMock(return_value=fake)):
        assert await leader_lock.confirm_leader_owned() is True


def test_infra_skip_reason_mysql_codes():
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from app.services.scheduler_admin import infra_skip_reason, is_retryable_infra

    class _Orig(Exception):
        def __init__(self, code: int, msg: str):
            self.args = (code, msg)
            super().__init__(code, msg)

    op = OperationalError("stmt", {}, _Orig(1049, "Unknown database 'smart_noodle_house'"))
    assert infra_skip_reason(op) == "db_infra:1049"
    assert is_retryable_infra(op) is False

    gone = OperationalError("stmt", {}, _Orig(2006, "MySQL server has gone away"))
    assert infra_skip_reason(gone) == "db_infra:2006"
    assert is_retryable_infra(gone) is True

    missing = ProgrammingError(
        "stmt", {}, _Orig(1146, "Table 'smart_noodle_house.webhook_inbox' doesn't exist")
    )
    assert infra_skip_reason(missing) == "db_infra:1146"
    assert is_retryable_infra(missing) is False

    assert infra_skip_reason(ValueError("business boom")) is None
    assert infra_skip_reason(asyncio.CancelledError()) == "shutdown_cancelled"


@pytest.mark.asyncio
async def test_leader_job_infra_skip_not_error():
    """库不存在等瞬态：记 skipped，不抛，不记 error。"""
    from app.core import leader_lock
    from app.services import scheduler_admin as sa
    from sqlalchemy.exc import OperationalError

    class _Orig(Exception):
        def __init__(self):
            self.args = (1049, "Unknown database 'x'")
            super().__init__(1049, "Unknown database 'x'")

    calls: list[dict] = []

    async def _record(job_id, *, status, duration_ms, error=None):
        calls.append({"job_id": job_id, "status": status, "error": error})

    async def boom():
        raise OperationalError("SELECT 1", {}, _Orig())

    boom.__name__ = "drain_webhook_inbox"
    wrapped = sa.leader_job(boom)

    leader_lock.set_leader_held(True)
    leader_lock.set_leader_worker_id("w-test")
    fake = MagicMock()
    fake.get = AsyncMock(return_value=b"w-test")

    with (
        patch("app.core.leader_lock.get_redis", AsyncMock(return_value=fake)),
        patch.object(sa, "record_job_run", AsyncMock(side_effect=_record)),
        patch.object(sa, "upsert_next_run", AsyncMock()),
        patch.object(sa, "is_job_enabled", return_value=True),
    ):
        out = await wrapped()
    assert out is None
    assert calls and calls[-1]["status"] == "skipped"
    assert "1049" in (calls[-1]["error"] or "")


@pytest.mark.asyncio
async def test_leader_job_business_error_still_raises():
    from app.core import leader_lock
    from app.services import scheduler_admin as sa

    calls: list[dict] = []

    async def _record(job_id, *, status, duration_ms, error=None):
        calls.append({"status": status, "error": error})

    async def boom():
        raise RuntimeError("logic bug")

    boom.__name__ = "close_expired_orders"
    wrapped = sa.leader_job(boom)

    leader_lock.set_leader_held(True)
    leader_lock.set_leader_worker_id("w-test")
    fake = MagicMock()
    fake.get = AsyncMock(return_value=b"w-test")

    with (
        patch("app.core.leader_lock.get_redis", AsyncMock(return_value=fake)),
        patch.object(sa, "record_job_run", AsyncMock(side_effect=_record)),
        patch.object(sa, "upsert_next_run", AsyncMock()),
        patch.object(sa, "is_job_enabled", return_value=True),
    ):
        with pytest.raises(RuntimeError, match="logic bug"):
            await wrapped()
    assert calls and calls[-1]["status"] == "error"


@pytest.mark.asyncio
async def test_leader_loss_cancels_inflight_wrapped_jobs():
    from app.core import leader_lock
    from app.services import scheduler_admin as sa

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def long_job():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    long_job.__name__ = "test_long_leader_job"
    wrapped = sa.leader_job(long_job)
    leader_lock.set_leader_held(True)
    leader_lock.set_leader_worker_id("w-cancel-test")
    fake = MagicMock()
    fake.get = AsyncMock(return_value=b"w-cancel-test")

    with (
        patch("app.core.leader_lock.get_redis", AsyncMock(return_value=fake)),
        patch.object(sa, "record_job_run", AsyncMock()),
        patch.object(sa, "upsert_next_run", AsyncMock()),
        patch.object(sa, "is_job_enabled", return_value=True),
    ):
        task = asyncio.create_task(wrapped())
        await asyncio.wait_for(started.wait(), timeout=1)
        assert task in sa._active_leader_job_tasks
        count = await sa.cancel_active_leader_jobs(timeout=1)
        assert count == 1
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cancelled.is_set()
    assert task not in sa._active_leader_job_tasks


@pytest.mark.asyncio
async def test_leader_job_does_not_replay_after_ambiguous_database_disconnect():
    from app.core import leader_lock
    from app.services import scheduler_admin as sa
    from sqlalchemy.exc import OperationalError

    class _Orig(Exception):
        def __init__(self):
            self.args = (2013, "Lost connection during query")
            super().__init__(2013, "Lost connection during query")

    calls = 0
    statuses = []

    async def maybe_committed_then_disconnected():
        nonlocal calls
        calls += 1
        raise OperationalError("COMMIT", {}, _Orig())

    async def record(job_id, *, status, duration_ms, error=None):
        statuses.append(status)

    maybe_committed_then_disconnected.__name__ = "ambiguous_commit_job"
    wrapped = sa.leader_job(maybe_committed_then_disconnected)
    leader_lock.set_leader_held(True)
    leader_lock.set_leader_worker_id("w-ambiguous")
    fake = MagicMock()
    fake.get = AsyncMock(return_value=b"w-ambiguous")

    with (
        patch("app.core.leader_lock.get_redis", AsyncMock(return_value=fake)),
        patch.object(sa, "record_job_run", side_effect=record),
        patch.object(sa, "upsert_next_run", AsyncMock()),
        patch.object(sa, "is_job_enabled", return_value=True),
    ):
        assert await wrapped() is None
    assert calls == 1
    assert statuses == ["skipped"]



@pytest.mark.asyncio
async def test_scheduler_stats_failure_does_not_swallow_leader_cancellation():
    from app.core import leader_lock
    from app.services import scheduler_admin as sa

    started = asyncio.Event()

    async def long_job():
        started.set()
        await asyncio.Event().wait()

    long_job.__name__ = "test_stats_failure_cancel"
    wrapped = sa.leader_job(long_job)
    leader_lock.set_leader_held(True)
    leader_lock.set_leader_worker_id("w-stats-cancel")
    fake = MagicMock()
    fake.get = AsyncMock(return_value=b"w-stats-cancel")

    with (
        patch("app.core.leader_lock.get_redis", AsyncMock(return_value=fake)),
        patch.object(sa, "record_job_run", AsyncMock(side_effect=RuntimeError("stats db down"))),
        patch.object(sa, "upsert_next_run", AsyncMock()),
        patch.object(sa, "is_job_enabled", return_value=True),
    ):
        task = asyncio.create_task(wrapped())
        await asyncio.wait_for(started.wait(), timeout=1)
        assert await sa.cancel_active_leader_jobs(timeout=1) == 1
        with pytest.raises(asyncio.CancelledError):
            await task

    assert task.done()
    assert task not in sa._active_leader_job_tasks


@pytest.mark.asyncio
async def test_scheduler_stats_failure_does_not_change_success_result():
    from app.core import leader_lock
    from app.services import scheduler_admin as sa

    async def successful_job():
        return "business-result"

    successful_job.__name__ = "test_stats_failure_success"
    wrapped = sa.leader_job(successful_job)
    leader_lock.set_leader_held(True)
    leader_lock.set_leader_worker_id("w-stats-success")
    fake = MagicMock()
    fake.get = AsyncMock(return_value=b"w-stats-success")

    with (
        patch("app.core.leader_lock.get_redis", AsyncMock(return_value=fake)),
        patch.object(sa, "record_job_run", AsyncMock(side_effect=RuntimeError("stats db down"))),
        patch.object(sa, "upsert_next_run", AsyncMock()),
        patch.object(sa, "is_job_enabled", return_value=True),
    ):
        assert await wrapped() == "business-result"



@pytest.mark.asyncio
async def test_active_leader_job_count_tracks_only_running_tasks():
    from app.services import scheduler_admin as sa

    finished = asyncio.Event()

    async def running():
        await finished.wait()

    task = asyncio.create_task(running())
    sa._active_leader_job_tasks.add(task)
    try:
        assert sa.active_leader_job_count() >= 1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        sa._active_leader_job_tasks.discard(task)

    assert sa.active_leader_job_count() == 0



@pytest.mark.asyncio
async def test_cancel_active_leader_jobs_has_bounded_wait_for_cancel_resistant_task():
    from app.services import scheduler_admin as sa

    release = asyncio.Event()

    async def cancellation_resistant():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # 模拟第三方 I/O / 任务错误地吞掉取消信号。
            await release.wait()

    task = asyncio.create_task(cancellation_resistant())
    sa._active_leader_job_tasks.add(task)
    try:
        count = await sa.cancel_active_leader_jobs(timeout=0.1)
        assert count == 1
        assert not task.done()
        assert sa.active_leader_job_count() >= 1
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=1)
        sa._active_leader_job_tasks.discard(task)

    assert sa.active_leader_job_count() == 0



@pytest.fixture(autouse=True)
def _activate_test_database_leader_epoch(monkeypatch):
    from unittest.mock import AsyncMock

    from app.core import leader_fencing

    leader_fencing.activate_leader_epoch(100, "test-worker")
    monkeypatch.setattr(
        leader_fencing,
        "confirm_database_epoch",
        AsyncMock(return_value=True),
    )
    yield
    leader_fencing.deactivate_leader_epoch("test-worker")

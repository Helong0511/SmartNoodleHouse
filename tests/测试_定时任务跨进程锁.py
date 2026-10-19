"""定时任务调度：跨进程覆盖通知、任务锁、Leader fence。"""
from __future__ import annotations

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
async def test_redis_job_lock_fail_open_on_redis_error():
    from app.services.scheduler_admin import redis_job_lock

    with patch("app.core.redis_client.get_redis", AsyncMock(side_effect=RuntimeError("down"))):
        async with redis_job_lock("t2") as ok:
            assert ok is True


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

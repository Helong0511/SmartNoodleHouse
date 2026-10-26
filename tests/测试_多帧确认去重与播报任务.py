# -*- coding: utf-8 -*-
"""多帧近重复不计入确认；播报 task_id 状态可读写。"""
from __future__ import annotations

import numpy as np
import pytest

from app.services.face_extractor import EMBEDDING_DIM
from app.services.face_liveness import MultiFrameTracker


def _unit(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-8)


def test_multiframe_ttl_uses_monotonic(monkeypatch):
    """确认窗 TTL 须跟 monotonic，避免墙上时钟回拨破坏确认。"""
    import time as _time

    tr = MultiFrameTracker(confirm_frames=3, ttl_seconds=1.0, duplicate_cos=0.999)
    emb = _unit(3)
    mono = {"v": 1000.0}
    monkeypatch.setattr(_time, "monotonic", lambda: mono["v"])
    ok1, _, _ = tr.push(1, emb, candidate_id=9, similarity=0.9)
    assert ok1 is False
    mono["v"] += 0.2
    ok2, _, _ = tr.push(1, _unit(4), candidate_id=9, similarity=0.91)
    assert ok2 is False
    # 跳过 TTL：应重置，不得带着旧帧确认
    mono["v"] += 2.0
    ok3, cid, _ = tr.push(1, _unit(5), candidate_id=9, similarity=0.92)
    assert ok3 is False
    assert cid == 9


def test_multiframe_rejects_near_duplicate_embeddings():
    tr = MultiFrameTracker(confirm_frames=2, ttl_seconds=5.0, duplicate_cos=0.995)
    emb = _unit(7)
    ok1, cid1, _ = tr.push(1, emb, candidate_id=42, similarity=0.9)
    assert ok1 is False
    # 同一向量再推：不得确认
    ok2, cid2, _ = tr.push(1, emb.copy(), candidate_id=42, similarity=0.91)
    assert ok2 is False
    assert cid2 == 42
    # 明显不同的第二帧才确认
    ok3, cid3, avg = tr.push(1, _unit(8), candidate_id=42, similarity=0.88)
    assert ok3 is True
    assert cid3 == 42
    assert avg >= 0.88


@pytest.mark.asyncio
async def test_speak_task_status_roundtrip(monkeypatch):
    from app.services.face_service import WelcomeService

    store: dict[str, str] = {}

    class _FakeRedis:
        async def set(self, key, value, ex=None):
            store[key] = value
            return True

        async def get(self, key):
            return store.get(key)

    async def _fake_get_redis():
        return _FakeRedis()

    monkeypatch.setattr("app.services.face_service.get_redis", _fake_get_redis)
    tid = "abc123task"
    await WelcomeService.set_speak_task_status(
        tid, "queued", camera_id=9, kind="test_speak_camera"
    )
    data = await WelcomeService.get_speak_task(tid)
    assert data is not None
    assert data["status"] == "queued"
    assert data["camera_id"] == 9
    await WelcomeService.set_speak_task_status(
        tid, "completed", device_playback="unverified"
    )
    data2 = await WelcomeService.get_speak_task(tid)
    assert data2["status"] == "completed"
    assert data2["device_playback"] == "unverified"


@pytest.mark.asyncio
async def test_enqueue_test_speak_marks_replaced_cancelled(monkeypatch):
    """同机位试听连点：旧 pending 必须写 cancelled/reason=replaced。"""
    import json

    from app.services.face_service import WelcomeService

    store: dict[str, str] = {}
    queue: list[str] = [
        json.dumps(
            {
                "kind": "test_speak_camera",
                "text": "旧试听",
                "camera_id": 3,
                "task_id": "old-tid",
                "enqueued_at": 1.0,
            },
            ensure_ascii=False,
        )
    ]

    class _Pipe:
        def __init__(self, q: list[str]):
            self.q = q
            self.ops: list = []

        def lrem(self, key, count, value):
            self.ops.append(("lrem", key, count, value))

        def rpush(self, key, *vals):
            self.ops.append(("rpush", key, vals))

        def ltrim(self, key, start, end):
            self.ops.append(("ltrim", key, start, end))

        async def execute(self):
            for op in self.ops:
                if op[0] == "lrem":
                    val = str(op[3])
                    if val in self.q:
                        self.q.remove(val)
                elif op[0] == "rpush":
                    self.q.extend(str(v) for v in op[2])
            self.ops.clear()
            return True

    class _FakeRedis:
        def pipeline(self):
            return _Pipe(queue)

        async def lrange(self, key, start, end):
            return list(queue)

        async def set(self, key, value, ex=None):
            store[key] = str(value)
            return True

        async def get(self, key):
            return store.get(key)

        async def eval(self, script, numkeys, *args):
            key = args[0]
            floor = int(args[1])
            cur = int(store.get(key) or 0)
            nxt = max(cur, floor) + 1
            store[key] = str(nxt)
            return nxt

    async def _gr():
        return _FakeRedis()

    monkeypatch.setattr("app.services.face_service.get_redis", _gr)
    monkeypatch.setattr(
        WelcomeService, "_schedule_g711_warmup", staticmethod(lambda *_a, **_k: None)
    )

    new_tid = await WelcomeService.enqueue(
        {
            "kind": "test_speak_camera",
            "text": "试听播报",
            "camera_id": 3,
        }
    )
    assert new_tid
    old = await WelcomeService.get_speak_task("old-tid")
    assert old is not None
    assert old["status"] == "cancelled"
    assert old.get("reason") == "replaced"


def test_pending_drop_reason_member_preempts_test_and_old_member():
    from app.services.face_service import WelcomeService

    assert (
        WelcomeService._pending_drop_reason("member", "unknown_visitor", True)
        == "member_preempt"
    )
    assert (
        WelcomeService._pending_drop_reason("member", "test_speak_camera", True)
        == "member_preempt"
    )
    assert WelcomeService._pending_drop_reason("member", "member", True) == "replaced"
    assert WelcomeService._pending_drop_reason("member", "test_speak_pc", True) == ""
    assert (
        WelcomeService._pending_drop_reason("test_speak_camera", "test_speak_camera", True)
        == "replaced"
    )


@pytest.mark.asyncio
async def test_enqueue_member_preempts_pending_test_speak(monkeypatch):
    """会员入队须作废同机位待播摄像头试听，避免试听挡门迎宾。"""
    import json

    from app.services.face_service import WelcomeService

    store: dict[str, str] = {}
    queue: list[str] = [
        json.dumps(
            {
                "kind": "test_speak_camera",
                "text": "试听播报",
                "camera_id": 7,
                "task_id": "tst-tid",
                "enqueued_at": 1.0,
            },
            ensure_ascii=False,
        )
    ]

    class _Pipe:
        def __init__(self, q: list[str]):
            self.q = q
            self.ops: list = []

        def lrem(self, key, count, value):
            self.ops.append(("lrem", key, count, value))

        def rpush(self, key, *vals):
            self.ops.append(("rpush", key, vals))

        def ltrim(self, key, start, end):
            self.ops.append(("ltrim", key, start, end))

        async def execute(self):
            for op in self.ops:
                if op[0] == "lrem":
                    val = str(op[3])
                    if val in self.q:
                        self.q.remove(val)
                elif op[0] == "rpush":
                    self.q.extend(str(v) for v in op[2])
            self.ops.clear()
            return True

    class _FakeRedis:
        def pipeline(self):
            return _Pipe(queue)

        async def lrange(self, key, start, end):
            return list(queue)

        async def set(self, key, value, ex=None):
            store[key] = str(value)
            return True

        async def get(self, key):
            return store.get(key)

        async def eval(self, script, numkeys, *args):
            key = args[0]
            floor = int(args[1])
            cur = int(store.get(key) or 0)
            nxt = max(cur, floor) + 1
            store[key] = str(nxt)
            return nxt

    async def _gr():
        return _FakeRedis()

    monkeypatch.setattr("app.services.face_service.get_redis", _gr)
    monkeypatch.setattr(
        WelcomeService, "_schedule_g711_warmup", staticmethod(lambda *_a, **_k: None)
    )
    tid = await WelcomeService.enqueue(
        {"kind": "member", "text": "欢迎回来，李四。", "camera_id": 7}
    )
    assert tid
    old = await WelcomeService.get_speak_task("tst-tid")
    assert old is not None
    assert old["status"] == "cancelled"
    assert old.get("reason") == "member_preempt"


@pytest.mark.asyncio
async def test_enqueue_unknown_skipped_when_member_pending(monkeypatch):
    """同机位已有会员待播时，陌生迎宾不得入队，task 记 member_pending。"""
    import json

    from app.services.face_service import WelcomeService

    store: dict[str, str] = {}
    queue: list[str] = [
        json.dumps(
            {
                "kind": "member",
                "text": "欢迎回来，张三。",
                "camera_id": 5,
                "task_id": "mem-tid",
                "enqueued_at": 1.0,
            },
            ensure_ascii=False,
        )
    ]

    class _FakeRedis:
        def pipeline(self):
            raise AssertionError("member_pending 路径不应写队列")

        async def lrange(self, key, start, end):
            return list(queue)

        async def set(self, key, value, ex=None):
            store[key] = str(value)
            return True

        async def get(self, key):
            return store.get(key)

    async def _gr():
        return _FakeRedis()

    monkeypatch.setattr("app.services.face_service.get_redis", _gr)
    monkeypatch.setattr(
        WelcomeService, "_schedule_g711_warmup", staticmethod(lambda *_a, **_k: None)
    )

    tid = await WelcomeService.enqueue(
        {
            "kind": "unknown_visitor",
            "text": "欢迎光临",
            "camera_id": 5,
            "task_id": "unk-tid",
        }
    )
    assert tid is None
    assert len(queue) == 1
    task = await WelcomeService.get_speak_task("unk-tid")
    assert task is not None
    assert task["status"] == "cancelled"
    assert task.get("reason") == "member_pending"


@pytest.mark.asyncio
async def test_memory_enqueue_unknown_skipped_when_member_pending(monkeypatch):
    """Redis 不可用时，内存队列仍须遵守 member_pending。"""
    WelcomeService = __import__(
        "app.services.face_service", fromlist=["WelcomeService"]
    ).WelcomeService

    WelcomeService._queue = None
    q = WelcomeService.get_queue()
    q.put_nowait(
        {
            "kind": "member",
            "text": "会员先播",
            "camera_id": 11,
            "task_id": "mem-m",
            "enqueued_at": 1.0,
        }
    )

    store: dict[str, str] = {}

    class _FakeRedis:
        async def lrange(self, *a, **k):
            raise RuntimeError("force memory")

        def pipeline(self):
            raise RuntimeError("force memory")

        async def set(self, key, value, ex=None):
            store[key] = str(value)
            return True

        async def get(self, key):
            return store.get(key)

        async def eval(self, *a, **k):
            return 1

    async def _gr():
        return _FakeRedis()

    monkeypatch.setattr("app.services.face_service.get_redis", _gr)
    monkeypatch.setattr(
        WelcomeService, "_schedule_g711_warmup", staticmethod(lambda *_a, **_k: None)
    )

    tid = await WelcomeService.enqueue(
        {
            "kind": "unknown_visitor",
            "text": "欢迎光临",
            "camera_id": 11,
            "task_id": "mem-u",
        }
    )
    assert tid is None
    assert q.qsize() == 1
    task = await WelcomeService.get_speak_task("mem-u")
    assert task is not None
    assert task.get("reason") == "member_pending"
    WelcomeService._queue = None


@pytest.mark.asyncio
async def test_try_welcome_releases_cooldown_when_enqueue_none(monkeypatch):
    """enqueue 返回 None 时会员冷却必须释放。"""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.face_service import WelcomeService

    db = MagicMock()
    customer = MagicMock()
    customer.id = 42
    customer.display_name = "测试"
    customer.nickname = None
    customer.level_id = None

    async def _get(model, pk):
        if model.__name__ == "Customer" or getattr(model, "__name__", "") == "Customer":
            return customer
        return None

    db.get = AsyncMock(side_effect=lambda model, pk: customer if pk == 42 else None)
    db.add = MagicMock()
    db.flush = AsyncMock()

    released: list[int] = []

    async def _release(cid):
        released.append(int(cid))

    with (
        patch.object(WelcomeService, "try_acquire_cooldown", new=AsyncMock(return_value=True)),
        patch.object(WelcomeService, "release_cooldown", new=AsyncMock(side_effect=_release)),
        patch.object(
            WelcomeService, "resolve_welcome_identity", new=AsyncMock(return_value=("测试", ""))
        ),
        patch.object(WelcomeService, "enqueue", new=AsyncMock(return_value=None)),
        patch(
            "app.services.system_settings_service.SystemSettingsService.effective_float",
            new=AsyncMock(return_value=0.35),
        ),
        patch(
            "app.services.system_settings_service.SystemSettingsService.effective_setting",
            new=AsyncMock(side_effect=lambda *a, **k: k.get("fallback") or a[2] if len(a) > 2 else ""),
        ),
    ):
        # effective_setting is called with (db, key, store_id=, fallback=)
        async def _setting(db, key, store_id=None, fallback=None):
            return fallback

        with patch(
            "app.services.system_settings_service.SystemSettingsService.effective_setting",
            new=AsyncMock(side_effect=_setting),
        ):
            ok = await WelcomeService.try_welcome(
                db, 42, camera_id=None, similarity=0.9, preconfirmed=True
            )
    assert ok is False
    assert released == [42]


def test_pick_face_prefers_area_times_det_score():
    from types import SimpleNamespace

    from app.services.face_extractor import _pick_largest_face

    big_weak = SimpleNamespace(bbox=[0, 0, 100, 100], det_score=0.2)
    small_strong = SimpleNamespace(bbox=[0, 0, 60, 60], det_score=0.95)
    # 10000*0.2=2000 < 3600*0.95=3420 → 应选强置信近脸
    picked = _pick_largest_face([big_weak, small_strong])
    assert picked is small_strong

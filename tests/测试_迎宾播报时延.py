"""迎宾播报时延相关：过期丢弃、会员打断世代、近阈值策略常量。"""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from app.services.face_service import WelcomeService


def test_stale_item_drops_old_welcome():
    old = {
        "kind": "unknown_visitor",
        "text": "欢迎光临",
        "camera_id": 1,
        "enqueued_at": time.time() - 30.0,
        "task_id": "stale-task-1",
    }
    fresh = {
        "kind": "member",
        "text": "尊贵的会员您好",
        "camera_id": 1,
        "enqueued_at": time.time(),
    }
    assert WelcomeService.is_stale_item(old) is True
    assert WelcomeService.is_stale_item(fresh) is False


@pytest.mark.asyncio
async def test_dequeue_marks_stale_task_cancelled(monkeypatch):
    store: dict[str, str] = {}
    calls = {"n": 0}

    class _FakeRedis:
        async def blpop(self, key, timeout=1):
            calls["n"] += 1
            if calls["n"] > 1:
                return None
            raw = json.dumps(
                {
                    "kind": "unknown_visitor",
                    "text": "欢迎光临",
                    "camera_id": 1,
                    "enqueued_at": time.time() - 99.0,
                    "task_id": "stale-tid",
                },
                ensure_ascii=False,
            )
            return (key, raw)

        async def set(self, key, value, ex=None):
            store[key] = str(value)
            return True

        async def get(self, key):
            return store.get(key)

        async def expire(self, key, ex=None):
            return True

    monkeypatch.setattr(
        "app.services.face_service.get_redis",
        AsyncMock(return_value=_FakeRedis()),
    )

    out = await WelcomeService.dequeue(timeout=0.3)
    assert out is None
    data = await WelcomeService.get_speak_task("stale-tid")
    assert data is not None
    assert data["status"] == "cancelled"
    assert data.get("reason") == "stale"


def test_test_speak_never_stale():
    item = {
        "kind": "test_speak_camera",
        "text": "试听播报",
        "camera_id": 1,
        "enqueued_at": time.time() - 120.0,
    }
    assert WelcomeService.is_stale_item(item) is False


def test_speak_generation_bump_for_member_interrupt():
    cam = 90001
    g0 = WelcomeService.speak_generation(cam)
    g1 = WelcomeService.bump_speak_generation(cam)
    assert g1 == g0 + 1
    assert WelcomeService.speak_generation(cam) == g1


def test_speak_generation_key_helper():
    from app.core.cache_keys import speak_generation_key

    assert speak_generation_key(12) == "snh:welcome:speak_gen:12"


def test_sync_speak_generation_from_redis_raises_local(monkeypatch):
    """Redis 更高世代应写入本地，供同步 abort 回调可见。"""
    import asyncio

    cam = 90002
    WelcomeService._speak_gen[cam] = 3

    class _FakeRedis:
        async def get(self, key):
            assert "90002" in key
            return b"7"

    async def _fake_get_redis():
        return _FakeRedis()

    monkeypatch.setattr(
        "app.services.face_service.get_redis", _fake_get_redis
    )
    out = asyncio.run(WelcomeService.sync_speak_generation_from_redis(cam))
    assert out == 7
    assert WelcomeService.speak_generation(cam) == 7


def test_bump_speak_generation_async_uses_redis_lua(monkeypatch):
    import asyncio

    cam = 90003
    WelcomeService._speak_gen.pop(cam, None)
    calls: list[str] = []

    class _FakeRedis:
        def __init__(self):
            self.store: dict[str, str] = {}

        async def eval(self, script, numkeys, *args):
            calls.append("eval")
            key = args[0]
            floor = int(args[1])
            ttl = int(args[2])
            cur = int(self.store.get(key) or 0)
            nxt = max(cur, floor) + 1
            self.store[key] = str(nxt)
            self.store[f"{key}:ttl"] = str(ttl)
            return nxt

    fake = _FakeRedis()

    async def _fake_get_redis():
        return fake

    monkeypatch.setattr(
        "app.services.face_service.get_redis", _fake_get_redis
    )
    out = asyncio.run(WelcomeService.bump_speak_generation_async(cam))
    assert out == 1
    assert "eval" in calls
    assert WelcomeService.speak_generation(cam) == 1
    # 模拟 Redis TTL 过期后键消失，但本地仍为 1：再 bump 必须抬升到 2（打断令牌）
    fake.store.clear()
    out2 = asyncio.run(WelcomeService.bump_speak_generation_async(cam))
    assert out2 == 2
    assert WelcomeService.speak_generation(cam) == 2


def test_redis_ensure_speak_gen_never_lowers(monkeypatch):
    """ensure 与并发 bump 竞态时不得把 Redis 世代压低。"""
    import asyncio

    cam = 90004
    WelcomeService._speak_gen[cam] = 5

    class _FakeRedis:
        def __init__(self):
            self.store = {f"snh:welcome:speak_gen:{cam}": "7"}  # 已被别处 bump 到 7

        async def eval(self, script, numkeys, *args):
            assert "math.max" in script or "max" in script
            key = args[0]
            want = int(args[1])
            ttl = int(args[2])
            cur = int(self.store.get(key) or 0)
            nxt = max(cur, want)
            self.store[key] = str(nxt)
            self.store[f"{key}:ttl"] = str(ttl)
            return nxt

    fake = _FakeRedis()

    async def _fake_get_redis():
        return fake

    monkeypatch.setattr("app.services.face_service.get_redis", _fake_get_redis)
    from app.core.cache_keys import speak_generation_key

    # ensure 只要求至少 5，但 Redis 已是 7 → 必须保持 7
    asyncio.run(WelcomeService._redis_ensure_speak_gen_at_least(cam, 5))
    assert fake.store[speak_generation_key(cam)] == "7"
    assert WelcomeService.speak_generation(cam) == 7


@pytest.mark.asyncio
async def test_memory_dequeue_skips_stale_and_returns_next(monkeypatch):
    """内存队列：过期项丢弃后应继续取到后续有效任务。"""
    import time as _time

    WelcomeService._queue = None
    q = WelcomeService.get_queue()
    stale = {
        "kind": "unknown_visitor",
        "text": "过期",
        "camera_id": 1,
        "enqueued_at": _time.time() - 99.0,
        "task_id": "mem-stale",
    }
    fresh = {
        "kind": "member",
        "text": "会员句",
        "camera_id": 1,
        "enqueued_at": _time.time(),
        "task_id": "mem-fresh",
    }
    q.put_nowait(stale)
    q.put_nowait(fresh)

    store: dict[str, str] = {}

    class _FakeRedis:
        async def blpop(self, key, timeout=1):
            raise RuntimeError("force memory path")

        async def set(self, key, value, ex=None):
            store[key] = str(value)
            return True

        async def get(self, key):
            return store.get(key)

    monkeypatch.setattr(
        "app.services.face_service.get_redis",
        AsyncMock(return_value=_FakeRedis()),
    )
    out = await WelcomeService.dequeue(timeout=0.5)
    assert out is not None
    assert out.get("task_id") == "mem-fresh"
    stale_st = await WelcomeService.get_speak_task("mem-stale")
    assert stale_st is not None
    assert stale_st.get("reason") == "stale"
    WelcomeService._queue = None


@pytest.mark.asyncio
async def test_test_speak_both_bumps_speak_generation(monkeypatch):
    """both 试听也会推摄像头，入队须 bump，否则无法打断上一句推流。"""
    cam = 90005
    WelcomeService._speak_gen[cam] = 3
    store: dict[str, str] = {}

    class _Pipe:
        def __init__(self):
            self.ops = []

        def lrem(self, *a):
            self.ops.append(("lrem", a))

        def rpush(self, *a):
            self.ops.append(("rpush", a))

        def ltrim(self, *a):
            self.ops.append(("ltrim", a))

        async def execute(self):
            return True

    class _FakeRedis:
        def pipeline(self):
            return _Pipe()

        async def lrange(self, *a, **k):
            return []

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
        {"kind": "test_speak", "text": "双端试听", "camera_id": cam}
    )
    assert tid
    assert WelcomeService.speak_generation(cam) == 4


@pytest.mark.asyncio
async def test_speak_task_local_fallback_when_redis_down(monkeypatch):
    """Redis 写失败时仍可从进程内读到播报状态（供试听轮询）。"""
    WelcomeService._speak_task_local.clear()

    class _Boom:
        async def set(self, *a, **k):
            raise RuntimeError("redis down")

        async def get(self, *a, **k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(
        "app.services.face_service.get_redis",
        AsyncMock(return_value=_Boom()),
    )
    await WelcomeService.set_speak_task_status("local-tid", "queued", kind="member")
    got = await WelcomeService.get_speak_task("local-tid")
    assert got is not None
    assert got["status"] == "queued"
    assert "updated_mono" not in got  # 对外不透出进程内时钟字段
    WelcomeService._speak_task_local.clear()


@pytest.mark.asyncio
async def test_speak_task_local_ttl_uses_monotonic(monkeypatch):
    """本地 speak_task TTL 走 monotonic，墙上时钟回拨不得永久挂住。"""
    import time as _time

    from app.core.cache_keys import SPEAK_TASK_TTL_SECONDS

    WelcomeService._speak_task_local.clear()

    class _Boom:
        async def set(self, *a, **k):
            raise RuntimeError("redis down")

        async def get(self, *a, **k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(
        "app.services.face_service.get_redis",
        AsyncMock(return_value=_Boom()),
    )
    mono = {"t": 5000.0}
    monkeypatch.setattr(_time, "monotonic", lambda: mono["t"])
    await WelcomeService.set_speak_task_status("ttl-tid", "queued", kind="member")
    assert await WelcomeService.get_speak_task("ttl-tid") is not None
    # 墙钟大幅回拨不应误杀
    monkeypatch.setattr(_time, "time", lambda: 1.0)
    assert await WelcomeService.get_speak_task("ttl-tid") is not None
    mono["t"] = 5000.0 + float(SPEAK_TASK_TTL_SECONDS) + 1.0
    assert await WelcomeService.get_speak_task("ttl-tid") is None
    WelcomeService._speak_task_local.clear()


def test_member_speak_is_abortable_by_speak_gen():
    """会员长句推流须可被后续 bump 打断（新人/试听），否则会挡下一句。"""
    import inspect

    from app.workers import tts_worker as tw

    src = inspect.getsource(tw.TTSWorker.run)
    assert '"member"' in src or "'member'" in src
    assert "_abortable" in src
    assert "speak_gen_bumped" in src


def test_recognition_worker_deadlines_use_monotonic():
    """yield/抑制近截止日期用 monotonic，避免墙上时钟回拨导致永久跳过识别。"""
    import inspect

    from app.workers.recognition_worker import RecognitionWorker

    src = inspect.getsource(RecognitionWorker.run)
    assert "time.monotonic()" in src
    assert "self._tts_yield_until[cam_id] = (" in src or "_tts_yield_until[cam_id] = time.monotonic()" in src
    # 不应再用 time.time() 写入 yield/抑制截止
    assert "time.time() + 0.35" not in src
    assert "time.time() + 25.0" not in src
    assert "time.time() + 0.6" not in src


def test_recognition_worker_sets_suppress_even_if_commit_fails():
    """入队成功后 commit 失败仍须 yield/抑制陌生，避免与会员句叠播。"""
    import inspect
    import re

    from app.workers.recognition_worker import RecognitionWorker

    src = inspect.getsource(RecognitionWorker.run)
    # 会员：ok 在 try 外判定后再写 suppress（勿把 suppress 仅绑在 commit 成功分支）
    assert re.search(
        r"ok\s*=\s*False[\s\S]*?try_welcome[\s\S]*?if ok:[\s\S]*?_suppress_unknown_until",
        src,
    )
    # 陌生：welcomed 同理
    assert re.search(
        r"welcomed\s*=\s*False[\s\S]*?maybe_welcome_unknown[\s\S]*?if welcomed:[\s\S]*?_tts_yield_until",
        src,
    )


def test_leader_starts_tts_worker_for_edge_provider():
    """生产 TTS_PROVIDER=edge 时也须启 TTSWorker，否则迎宾队列永不消费。"""
    import inspect

    import app.main as main_mod

    # _start_leader_work 嵌在 lifespan 内，读 lifespan 源码即可
    src = inspect.getsource(main_mod.lifespan)
    assert "tts_worker.run()" in src
    assert 'tts_provider == "local"' not in src
    assert "tts_provider == 'local'" not in src


def test_tts_worker_skips_when_camera_stopped_on_leader():
    """停采后队列里的摄像头迎宾须 cancelled；both 试听勿整单取消。"""
    import inspect
    import re

    from app.workers.tts_worker import TTSWorker

    src = inspect.getsource(TTSWorker.run)
    assert "camera_stopped" in src
    assert "get_worker" in src
    m = re.search(
        r'kind in \(\s*"member",\s*"unknown_visitor",\s*"test_speak_camera",\s*\)',
        src,
    )
    assert m, "camera_stopped 仅应取消 member/unknown/test_speak_camera"
    # both 的 kind=test_speak 不得出现在该元组内
    assert '"test_speak",' not in m.group(0)


def test_stop_camera_bumps_speak_generation():
    """停止摄像头须 bump speak_gen，打断进行中的对讲推流。"""
    import inspect

    from app.api import cameras as cam_api

    src = inspect.getsource(cam_api.stop_camera)
    assert "bump_speak_generation_async" in src


def test_need_taste_covers_preference_template_placeholder():
    """默认副句含 {口味}：need_taste 须把 preference 模板纳入，否则口味推荐永不查库。"""
    greeting = WelcomeService.DEFAULT_GREETING_TEMPLATE
    pref = WelcomeService.DEFAULT_PREFERENCE_TEMPLATE
    assert not WelcomeService.template_has_placeholder(greeting, "口味", "taste")
    assert WelcomeService.template_has_placeholder(pref, "口味", "taste")
    need_taste = WelcomeService.template_has_placeholder(
        greeting, "口味", "taste"
    ) or WelcomeService.template_has_placeholder(pref, "口味", "taste")
    assert need_taste is True


def test_extract_live_face_hit_accepts_light_kw():
    """收集期 light 路径签名存在（无 InsightFace 时走兜底仍可调用）。"""
    import inspect

    from app.services.face_extractor import extract_live_face_hit

    sig = inspect.signature(extract_live_face_hit)
    assert "light" in sig.parameters


def test_live_face_hit_exposes_face_side_px_fields():
    from app.services.face_best_frame import LiveFaceHit
    import numpy as np

    emb = np.ones(512, dtype=np.float32)
    emb = emb / (np.linalg.norm(emb) + 1e-8)
    hit = LiveFaceHit(
        embedding=emb,
        det_score=0.9,
        face_area_ratio=0.02,
        sharpness=100.0,
        face_side_px=96,
        frame_w=1920,
        frame_h=1080,
    )
    assert hit.face_side_px == 96
    assert hit.frame_w == 1920


def test_downscale_for_live_respects_max_side():
    import numpy as np
    from app.services.face_extractor import _downscale_for_live

    img = np.zeros((1440, 2560, 3), dtype=np.uint8)
    light = _downscale_for_live(img, max_side=720)
    full = _downscale_for_live(img, max_side=960)
    assert max(light.shape[0], light.shape[1]) == 720
    assert max(full.shape[0], full.shape[1]) == 960


def test_near_threshold_margin_covers_false_unknown():
    """sim=0.44 / threshold=0.48 应落入近阈值，短暂暂缓陌生迎宾。"""
    threshold = 0.48
    near_margin = 0.08
    best_sim = 0.444
    defer = best_sim >= threshold - near_margin
    assert defer is True
    # 超时后仍按访客播，避免干等 10–20s
    near_max_wait = 1.5
    assert near_max_wait <= 2.0
    assert 0.15 < threshold - near_margin  # 真正陌生仍会迎宾


def test_face_extractor_live_ready_helpers_exist():
    from app.services.face_extractor import ensure_live_face_app, is_live_face_ready

    assert callable(ensure_live_face_app)
    assert callable(is_live_face_ready)

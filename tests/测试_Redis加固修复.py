"""Redis 加固修复验证测试：覆盖 jitter/互斥锁/空值标记/延迟双删/大Key/TTL补全等。"""
from __future__ import annotations

import asyncio
import json
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.cache_keys import ttl_with_jitter


# ── Fake Redis ──

class FakeRedis:
    """轻量异步 Fake Redis，记录操作用于断言。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple] = []
        self.delete_calls: list[str] = []
        self.incr_calls: list[str] = []
        self.expire_calls: list[tuple] = []
        self.eval_calls: list[tuple] = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, *, nx=None, ex=None):
        self.set_calls.append((key, value, nx, ex))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, *keys):
        deleted = 0
        for k in keys:
            self.delete_calls.append(k)
            if k in self.store:
                del self.store[k]
                deleted += 1
        return deleted

    async def incr(self, key):
        self.incr_calls.append(key)
        cur = int(self.store.get(key, 0)) + 1
        self.store[key] = str(cur)
        return cur

    async def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))
        return 1

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def eval(self, script, numkeys, key, token, *args):
        self.eval_calls.append((script, numkeys, key, token, args))
        # 模拟项目中使用的几类 Lua 原子脚本，避免把 compare-expire 错当成 delete。
        if "INCR" in script:
            count = int(self.store.get(key, 0)) + 1
            self.store[key] = str(count)
            if "EXPIRE" in script.upper():
                self.expire_calls.append((key, int(token)))
            return count
        if self.store.get(key) != str(token):
            return 0
        if "expire" in script.lower():
            return 1
        if "del" in script.lower():
            del self.store[key]
            return 1
        return 0

    def pipeline(self):
        outer = self

        class _Pipe:
            def __init__(self):
                self.cmds: list = []

            async def sadd(self, *args):
                self.cmds.append(("sadd", args))

            async def expire(self, key, ttl):
                self.cmds.append(("expire", key, ttl))

            async def rpush(self, *args):
                self.cmds.append(("rpush", args))

            async def ltrim(self, *args):
                pass

            async def lrange(self, *args):
                return []

            async def llen(self, *args):
                return 0

            async def incr(self, *args):
                pass

            async def get(self, key):
                return outer.store.get(key)

            async def execute(self):
                results = []
                for cmd in self.cmds:
                    if cmd[0] == "expire":
                        outer.expire_calls.append((cmd[1], cmd[2]))
                    results.append(0)
                return results

        return _Pipe()

    async def smembers(self, key):
        return set()

    async def srem(self, key, *members):
        return 1

    async def scan_iter(self, *, match=None, count=None):
        for k in list(self.store.keys()):
            if match and match.replace("*", "") in k:
                yield k

    async def ping(self):
        pass

    async def publish(self, *args):
        pass


def _patch_get_redis(fake: FakeRedis):
    async def _get():
        return fake
    return _get


# ── ttl_with_jitter ──

def test_ttl_with_jitter_within_bounds():
    for _ in range(100):
        result = ttl_with_jitter(100)
        assert 80 <= result <= 120


def test_ttl_with_jitter_minimum_1():
    assert ttl_with_jitter(0) >= 1
    assert ttl_with_jitter(-10) >= 1


def test_ttl_with_jitter_custom_ratio():
    result = ttl_with_jitter(100, min_ratio=0.9, max_ratio=1.1)
    assert 90 <= result <= 110


# ── menu_cache 互斥锁 ──

@pytest.mark.asyncio
async def test_menu_cache_rebuild_lock_acquire_release():
    from app.core import menu_cache
    fake = FakeRedis()
    with patch.object(menu_cache, "get_redis", _patch_get_redis(fake)):
        ok1 = await menu_cache.try_acquire_rebuild_lock(1)
        assert isinstance(ok1, str) and ok1
        # 第二次抢锁应失败（NX 互斥）
        ok2 = await menu_cache.try_acquire_rebuild_lock(1)
        assert ok2 is None
        await menu_cache.release_rebuild_lock(1, ok1)
        # 释放后可再次抢锁
        ok3 = await menu_cache.try_acquire_rebuild_lock(1)
        assert isinstance(ok3, str) and ok3
        assert ok3 in menu_cache._REBUILD_HEARTBEATS
        heartbeat = menu_cache._REBUILD_HEARTBEATS[ok3]
        await menu_cache.release_rebuild_lock(1, ok3)
        assert heartbeat.done()
        assert ok3 not in menu_cache._REBUILD_HEARTBEATS


# ── menu_cache 大Key 跳过 ──

@pytest.mark.asyncio
async def test_menu_cache_large_bundle_skipped():
    from app.core import menu_cache
    fake = FakeRedis()
    with patch.object(menu_cache, "get_redis", _patch_get_redis(fake)):
        # 构造超 256KB 的 bundle
        big_data = {"data": "x" * (300 * 1024)}
        await menu_cache.set_menu_bundle(1, big_data)
        # 不应写入 Redis
        assert menu_cache.menu_bundle_key(1) not in fake.store


@pytest.mark.asyncio
async def test_menu_cache_normal_bundle_cached():
    from app.core import menu_cache
    fake = FakeRedis()
    with patch.object(menu_cache, "get_redis", _patch_get_redis(fake)):
        bundle = {"categories": [{"id": 1, "name": "noodles"}]}
        await menu_cache.set_menu_bundle(1, bundle)
        assert menu_cache.menu_bundle_key(1) in fake.store


# ── menu_cache bump_menu_version TTL 补全 ──

@pytest.mark.asyncio
async def test_menu_cache_bump_always_expire():
    from app.core import menu_cache
    fake = FakeRedis()
    with patch.object(menu_cache, "get_redis", _patch_get_redis(fake)):
        # 第一次 bump（version 从 1 开始）：应该 expire
        v1 = await menu_cache.bump_menu_version(1)
        assert v1 == 1
        expire_keys = [k for k, _ in fake.expire_calls if "ver" in k]
        assert len(expire_keys) == 1
        # 第二次 bump：旧代码不 expire，修复后每次都 expire
        fake.expire_calls.clear()
        v2 = await menu_cache.bump_menu_version(1)
        assert v2 == 2
        expire_keys = [k for k, _ in fake.expire_calls if "ver" in k]
        assert len(expire_keys) == 1, "每次 incr 后都应补 expire"


# ── reco_cache 互斥锁 ──

@pytest.mark.asyncio
async def test_reco_cache_lock_acquire_release():
    from app.services import reco_cache
    fake = FakeRedis()
    with patch.object(reco_cache, "get_redis", _patch_get_redis(fake)):
        ok1 = await reco_cache.try_acquire_reco_lock(1, 1, "default", "a")
        assert isinstance(ok1, str) and ok1
        ok2 = await reco_cache.try_acquire_reco_lock(1, 1, "default", "a")
        assert ok2 is None
        await reco_cache.release_reco_lock(1, 1, "default", "a", token=ok1)
        ok3 = await reco_cache.try_acquire_reco_lock(1, 1, "default", "a")
        assert isinstance(ok3, str) and ok3
        assert ok3 in reco_cache._REBUILD_HEARTBEATS
        heartbeat = reco_cache._REBUILD_HEARTBEATS[ok3]
        await reco_cache.release_reco_lock(1, 1, "default", "a", token=ok3)
        assert heartbeat.done()
        assert ok3 not in reco_cache._REBUILD_HEARTBEATS


# ── reco_cache jitter TTL ──

@pytest.mark.asyncio
async def test_reco_cache_set_uses_jitter():
    from app.services import reco_cache
    fake = FakeRedis()
    with patch.object(reco_cache, "get_redis", _patch_get_redis(fake)):
        await reco_cache.set_cached_reco(None, 1, "default", "a", {"items": []}, "")
        key, value, nx, ex = fake.set_calls[-1]
        # TTL 应在 96-144 范围内（120 ±20%）
        assert ex is not None
        assert 96 <= ex <= 144


# ── reco_cache 代际失效：不扫描全量 key，阻止慢构建写回旧结果 ──

def test_reco_invalidation_is_deferred_until_database_commit():
    from sqlalchemy.orm import Session
    from app.services import reco_cache

    session = Session()
    scheduled = []

    def capture(coro):
        scheduled.append(coro)
        coro.close()

    with patch.object(reco_cache, "spawn_bg", side_effect=capture):
        session.begin()
        reco_cache.defer_invalidate_customer(session, 41)
        reco_cache.defer_invalidate_customer(session, 41)
        assert scheduled == []
        session.commit()

    assert len(scheduled) == 1
    session.close()


@pytest.mark.asyncio
async def test_reco_cache_publish_rejects_stale_customer_generation():
    from app.services import reco_cache
    from app.core.cache_keys import reco_generation_key

    class GenerationAwareFakeRedis(FakeRedis):
        async def eval(self, script, numkeys, lock_key, cache_key, generation_key,
                       token, raw, ttl, expected_generation):
            self.eval_calls.append((script, numkeys, lock_key, cache_key, generation_key,
                                    token, raw, ttl, expected_generation))
            if self.store.get(lock_key) != token:
                return 0
            if str(self.store.get(generation_key, "0")) != str(expected_generation):
                return 0
            self.store[cache_key] = raw
            return "OK"

    fake = GenerationAwareFakeRedis()
    fake.store[reco_cache.reco_rebuild_lock_key(41, 3, "menu", "v1", "", generation=0)] = "old-owner"
    fake.store[reco_generation_key(41)] = "1"

    with patch.object(reco_cache, "get_redis", _patch_get_redis(fake)):
        published = await reco_cache.set_cached_reco_if_owner(
            41, 3, "menu", "v1", {"items": [{"id": 7}]}, "", "old-owner",
            expected_generation=0,
        )

    assert published is False
    assert reco_cache.reco_key(41, 3, "menu", "v1", "", generation=0) not in fake.store


@pytest.mark.asyncio
async def test_privacy_profile_clear_schedules_recommendation_cache_invalidation():
    from types import SimpleNamespace
    from app.services import privacy_service, reco_cache

    class FakeResult:
        rowcount = 0
        def all(self):
            return []

    class FakeDb:
        async def execute(self, *_args, **_kwargs):
            return FakeResult()
        async def flush(self):
            return None

    scheduled = []
    with (
        patch.object(reco_cache, "defer_invalidate_customer",
                     side_effect=lambda db, cid: scheduled.append((db, cid))),
        patch.object(privacy_service.AuditService, "log", new_callable=AsyncMock),
    ):
        await privacy_service.PrivacyService.clear_taste_profile(
            FakeDb(), SimpleNamespace(id=987), request=None,
        )

    assert len(scheduled) == 1
    assert scheduled[0][1] == 987


def test_reco_invalidation_waits_for_outer_transaction_after_nested_savepoint():
    from sqlalchemy.orm import Session
    from app.services import reco_cache

    session = Session()
    scheduled = []

    def capture(coro):
        scheduled.append(coro)
        coro.close()

    with patch.object(reco_cache, "spawn_bg", side_effect=capture):
        with session.begin():
            with session.begin_nested():
                reco_cache.defer_invalidate_customer(session, 42)
            # SAVEPOINT 已提交，但外层业务事务仍未提交。
            assert scheduled == []
        # 只有外层事务提交后才允许推进 Redis 代际。
        assert len(scheduled) == 1

    session.close()


@pytest.mark.asyncio
async def test_reco_cache_generation_read_failure_never_reads_generation_zero():
    from app.services import reco_cache

    with patch.object(
        reco_cache, "get_redis", new_callable=AsyncMock,
        side_effect=RuntimeError("redis temporarily unavailable"),
    ):
        generation = await reco_cache.get_reco_generation(123)
        hit = await reco_cache.get_cached_reco(
            123, 1, "menu", "a", generation=generation,
        )

    assert generation is None
    assert hit is None


@pytest.mark.asyncio
async def test_reco_cache_invalidate_increments_generation_without_scan():
    from app.services import reco_cache
    from app.core.cache_keys import reco_generation_key

    fake = FakeRedis()
    fake.store["snh:reco:c1:s1:default:a:g0"] = "{}"
    with patch.object(reco_cache, "get_redis", _patch_get_redis(fake)):
        await reco_cache.invalidate_customer(1)

    assert fake.store[reco_generation_key(1)] == "1"
    assert fake.expire_calls[-1] == (reco_generation_key(1), 30 * 24 * 3600)
    assert len(fake.eval_calls) == 1
    # 代际隔离后不再执行 SCAN/DEL 风暴；旧 key 由短 TTL 自然回收。
    assert fake.delete_calls == []


# ── auth TTL 补全 ──

@pytest.mark.asyncio
async def test_auth_login_fail_always_expire():
    from app.api import auth
    from app.core import redis_client
    fake = FakeRedis()
    with patch.object(redis_client, "get_redis", _patch_get_redis(fake)):
        # 模拟登录失败计数键已存在（count=2，非 1）
        fake.store["snh:login:fail:test"] = "2"
        await auth._record_login_failure("test", "1.2.3.4")
        # 修复后每次 incr 后都 expire，不只是 count==1 时
        expire_keys = [k for k, _ in fake.expire_calls if "login:fail" in k]
        assert len(expire_keys) == 1, "incr 后应补 expire 防永久键"


# ── startup_checks 生产 Redis 校验 ──

def test_startup_checks_reject_no_password_prod():
    from app.core.startup_checks import validate_settings
    from types import SimpleNamespace

    settings = SimpleNamespace(
        is_production=True,
        app_env="production",
        app_debug=False,
        allow_payment_mock=False,
        allow_auth_mock=False,
        mini_program_env_version="release",
        allow_delivery_mock=False,
        allow_print_mock=False,
        allow_opencv_face_fallback=False,
        enable_rate_limit=True,
        secret_encryption_key="x" * 32,
        enable_customer_display=False,
        cors_origins="https://example.com",
        h5_public_base_url="https://example.com",
        storage_backend="s3",
        s3_endpoint="https://s3.example.com",
        s3_access_key="ak",
        s3_secret_key="sk",
        s3_bucket="bucket",
        webhook_ip_allowlist="10.0.0.1",
        webhook_require_allowlist=False,
        database_url="mysql://root:pass@10.0.0.1:3306/db",
        redis_url="redis://10.0.0.1:6379/0",  # 无密码
        process_role="auto",
        jwt_secret="a" * 32 + "B!c@d#",
        jwt_algorithm="HS256",
        face_encryption_key="b" * 32 + "C!d@e#",
    )
    with pytest.raises(SystemExit):
        validate_settings(settings)


def test_startup_checks_reject_localhost_prod():
    from app.core.startup_checks import validate_settings
    from types import SimpleNamespace

    settings = SimpleNamespace(
        is_production=True,
        app_env="production",
        app_debug=False,
        allow_payment_mock=False,
        allow_auth_mock=False,
        mini_program_env_version="release",
        allow_delivery_mock=False,
        allow_print_mock=False,
        allow_opencv_face_fallback=False,
        enable_rate_limit=True,
        secret_encryption_key="x" * 32,
        enable_customer_display=False,
        cors_origins="https://example.com",
        h5_public_base_url="https://example.com",
        storage_backend="s3",
        s3_endpoint="https://s3.example.com",
        s3_access_key="ak",
        s3_secret_key="sk",
        s3_bucket="bucket",
        webhook_ip_allowlist="10.0.0.1",
        webhook_require_allowlist=False,
        database_url="mysql://root:pass@10.0.0.1:3306/db",
        redis_url="redis://:pass@127.0.0.1:6379/0",  # localhost
        process_role="auto",
        jwt_secret="a" * 32 + "B!c@d#",
        jwt_algorithm="HS256",
        face_encryption_key="b" * 32 + "C!d@e#",
    )
    with pytest.raises(SystemExit):
        validate_settings(settings)


# ── payments Key 命名规范 ──

@pytest.mark.asyncio
async def test_payments_sync_cooldown_key_prefix():
    from app.api import payments
    fake = FakeRedis()
    with patch.object(payments, "get_redis", _patch_get_redis(fake)):
        try:
            await payments._acquire_sync_cooldown(42)
        except Exception:
            pass
        key = fake.set_calls[-1][0]
        assert key.startswith("snh:pay:sync:"), f"Key 须以 snh:pay:sync: 开头，实际为 {key}"


# ── perf_metrics 默认窗口和命令数限制 ──

def test_perf_metrics_default_window_1h():
    from app.core.perf_metrics import DEFAULT_QUERY_HOURS, WINDOW_HOURS
    assert DEFAULT_QUERY_HOURS == 1, "默认查询窗口应改为 1h 减少慢查询"
    assert WINDOW_HOURS == 24, "最大窗口保持 24h"


def test_perf_metrics_routes_set_ttl():
    from app.core.perf_metrics import _ROUTES_SET_TTL
    assert _ROUTES_SET_TTL == 48 * 3600, "路由 Set 应有 48h TTL"


def test_perf_metrics_max_pipe_commands():
    from app.core.perf_metrics import _MAX_PIPE_COMMANDS
    assert _MAX_PIPE_COMMANDS == 5000, "pipeline 命令数上限应为 5000"


# ── leader_lock 冗余 decode 清理 ──

@pytest.mark.asyncio
async def test_leader_lock_contender_never_overwrites_existing_owner():
    from app.core import leader_lock

    fake = FakeRedis()
    fake.store[leader_lock.LEADER_KEY] = "active-worker-A"
    leader_lock.set_leader_held(False)
    with patch.object(leader_lock, "get_redis", _patch_get_redis(fake)):
        acquired = await leader_lock.try_acquire_leader("new-worker-B")
    assert acquired is False
    assert fake.store[leader_lock.LEADER_KEY] == "active-worker-A"


@pytest.mark.asyncio
async def test_leader_lock_confirm_no_decode_call():
    from app.core import leader_lock
    fake = FakeRedis()
    fake.store["snh:process:leader"] = "worker_A"
    leader_lock.set_leader_held(True)
    leader_lock.set_leader_worker_id("worker_A")
    with patch.object(leader_lock, "get_redis", _patch_get_redis(fake)):
        result = await leader_lock.confirm_leader_owned()
        assert result is True


# ── system_settings_service TTL 补全 ──

@pytest.mark.asyncio
async def test_system_settings_bump_expire():
    from app.services import system_settings_service as svc
    from app.core import redis_client
    fake = FakeRedis()
    with patch.object(redis_client, "get_redis", _patch_get_redis(fake)):
        # 预填充版本键（模拟已有旧键 incr）
        fake.store[svc.REDIS_CACHE_VER_KEY] = "5"
        await svc.SystemSettingsService._bump_remote_version()
        expire_keys = [k for k, _ in fake.expire_calls if "system_settings" in k]
        assert len(expire_keys) == 1, "incr 后应补 expire"


# ── scheduler_admin TTL 补全 ──

@pytest.mark.asyncio
async def test_scheduler_admin_notify_expire(monkeypatch):
    from app.services import scheduler_admin
    from app.core import redis_client
    fake = FakeRedis()
    fake.store[scheduler_admin.OVERRIDES_VER_KEY] = "3"
    with patch.object(redis_client, "get_redis", _patch_get_redis(fake)):
        await scheduler_admin.notify_overrides_changed(job_id="test")
        expire_keys = [k for k, _ in fake.expire_calls if "scheduler" in k and "overrides" in k]
        assert len(expire_keys) == 1, "incr 后应补 expire"


@pytest.mark.asyncio
async def test_menu_rebuild_lock_old_owner_cannot_delete_new_owner():
    from app.core import menu_cache

    fake = FakeRedis()
    key = menu_cache.menu_rebuild_lock_key(5)
    fake.store[key] = "new-owner-token"
    with patch.object(menu_cache, "get_redis", _patch_get_redis(fake)):
        await menu_cache.release_rebuild_lock(5, "expired-old-owner-token")
    assert fake.store[key] == "new-owner-token"


@pytest.mark.asyncio
async def test_menu_cache_publish_requires_current_rebuild_owner():
    from app.core import menu_cache

    class OwnerAwareFakeRedis(FakeRedis):
        async def eval(self, script, numkeys, lock_key, cache_key, token, raw, ttl):
            self.eval_calls.append((script, numkeys, lock_key, cache_key, token, raw, ttl))
            if self.store.get(lock_key) != token:
                return 0
            self.store[cache_key] = raw
            return "OK"

    fake = OwnerAwareFakeRedis()
    lock_key = menu_cache.menu_rebuild_lock_key(5)
    cache_key = menu_cache.menu_bundle_key(5)
    fake.store[lock_key] = "new-owner"

    with patch.object(menu_cache, "get_redis", _patch_get_redis(fake)):
        stale = await menu_cache.set_menu_bundle_if_owner(
            5, {"products": []}, "expired-old-owner"
        )
        current = await menu_cache.set_menu_bundle_if_owner(
            5, {"products": [{"id": 9}]}, "new-owner"
        )

    assert stale is False
    assert current is True
    assert '"id":9' in fake.store[cache_key]


@pytest.mark.asyncio
async def test_reco_rebuild_lock_old_owner_cannot_delete_new_owner():
    from app.services import reco_cache

    fake = FakeRedis()
    key = "snh:reco:lock:c7:s5:default:b0:g0"
    fake.store[key] = "new-owner-token"
    with patch.object(reco_cache, "get_redis", _patch_get_redis(fake)):
        await reco_cache.release_reco_lock(7, 5, "default", "b0", token="expired-old-owner-token")
    assert fake.store[key] == "new-owner-token"


def test_recommendation_cache_identity_includes_limit():
    from app.api.miniprogram_menu import _reco_cache_fingerprint

    assert _reco_cache_fingerprint("", 5) != _reco_cache_fingerprint("", 20)
    assert _reco_cache_fingerprint("cart:abc", 5) != _reco_cache_fingerprint("cart:abc", 10)
    assert _reco_cache_fingerprint("", 10) == ":limit=10"


def test_rate_limit_bucket_collapses_dynamic_ids_and_keeps_sensitive_policies_separate():
    from app.core.rate_limit import _rate_limit_bucket

    assert _rate_limit_bucket("/api/orders/10001") == _rate_limit_bucket("/api/orders/99999")
    assert _rate_limit_bucket("/api/miniprogram/orders/10001") == "/api/miniprogram"
    assert _rate_limit_bucket("/api/auth/login") == "/api/auth/login"
    assert _rate_limit_bucket("/api/auth/verify-password") == "/api/auth/verify-password"
    assert _rate_limit_bucket("/api/miniprogram/orders/10001") == "/api/miniprogram/orders"
    assert _rate_limit_bucket("/api/miniprogram/menu/1") == "/api/miniprogram/menu"
    assert _rate_limit_bucket("/api/miniprogram/orders/10001") == _rate_limit_bucket("/api/miniprogram/orders/99999")


@pytest.mark.asyncio
async def test_ws_subscriber_closes_pubsub_connection_on_shutdown():
    from app.services import ws_pubsub

    class FakePubSub:
        def __init__(self):
            self.closed = False

        async def subscribe(self, channel):
            self.channel = channel

        async def listen(self):
            while True:
                await asyncio.sleep(1)
                yield None

        async def aclose(self):
            self.closed = True

    pubsub = FakePubSub()
    fake_redis = MagicMock()
    fake_redis.pubsub.return_value = pubsub

    with patch.object(ws_pubsub, "get_redis", new_callable=AsyncMock, return_value=fake_redis):
        await ws_pubsub.start_ws_subscriber(AsyncMock())
        await asyncio.sleep(0.01)
        await ws_pubsub.stop_ws_subscriber()

    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_local_singleflight_serializes_same_key_and_cleans_registry():
    from app.core import async_utils

    active = 0
    maximum = 0
    calls = 0

    async def worker():
        nonlocal active, maximum, calls
        async with async_utils.local_singleflight("test:redis:hot-key"):
            calls += 1
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.02)
            active -= 1

    await asyncio.gather(*(worker() for _ in range(12)))
    assert maximum == 1
    assert calls == 12
    assert "test:redis:hot-key" not in async_utils._SINGLEFLIGHT_LOCKS


@pytest.mark.asyncio
async def test_reco_cache_does_not_serve_personalized_local_fallback_when_redis_is_unavailable():
    from app.services import reco_cache

    reco_cache._LOCAL_RECO_CACHE.clear()
    payload = {"items": [{"id": 1}]}
    with patch.object(reco_cache, "get_redis", new_callable=AsyncMock, side_effect=RuntimeError("redis down")):
        await reco_cache.set_cached_reco(9, 2, "menu", "a", payload, "")
        hit = await reco_cache.get_cached_reco(9, 2, "menu", "a", "")
    # 顾客授权撤回/画像删除可能发生在其他 Worker；不能用进程内缓存绕过共享代际。
    assert hit is None
    assert reco_cache._get_local_reco(reco_cache._local_reco_key(9, 2, "menu", "a", "")) is None


@pytest.mark.asyncio
async def test_reco_cache_publish_requires_current_rebuild_owner():
    from app.services import reco_cache

    class OwnerAwareFakeRedis(FakeRedis):
        async def eval(self, script, numkeys, lock_key, cache_key, generation_key, token, raw, ttl, expected_generation):
            self.eval_calls.append((script, numkeys, lock_key, cache_key, generation_key, token, raw, ttl, expected_generation))
            if self.store.get(lock_key) != token:
                return 0
            if int(self.store.get(generation_key, "0")) != int(expected_generation):
                return 0
            self.store[cache_key] = raw
            return "OK"

    fake = OwnerAwareFakeRedis()
    lock_key = reco_cache.reco_rebuild_lock_key(7, 5, "menu", "a", "", generation=0)
    cache_key = reco_cache.reco_key(7, 5, "menu", "a", "", generation=0)
    fake.store[lock_key] = "current-owner"
    reco_cache._LOCAL_RECO_CACHE.clear()

    with patch.object(reco_cache, "get_redis", _patch_get_redis(fake)):
        stale = await reco_cache.set_cached_reco_if_owner(
            7, 5, "menu", "a", {"items": [{"id": 1}]}, "", "expired-owner",
            expected_generation=0,
        )
        current = await reco_cache.set_cached_reco_if_owner(
            7, 5, "menu", "a", {"items": [{"id": 2}]}, "", "current-owner",
            expected_generation=0,
        )

    assert stale is False
    assert current is True
    assert '"id":2' in fake.store[cache_key]
    # 顾客级推荐不进入进程内缓存，避免授权撤回后其他 Worker 继续返回旧画像。
    local = reco_cache._get_local_reco(reco_cache._local_reco_key(7, 5, "menu", "a", ""))
    assert local is None


@pytest.mark.asyncio
async def test_reco_cache_customer_invalidation_clears_local_fallback():
    from app.services import reco_cache

    reco_cache._LOCAL_RECO_CACHE.clear()
    # 模拟历史/错误路径残留的顾客级本地缓存，失效函数仍应清除本 Worker 的副本。
    reco_cache._set_local_reco(
        reco_cache._local_reco_key(19, 2, "menu", "a", ""), {"items": [1]}
    )
    reco_cache._invalidate_local_reco_customer(19)
    assert reco_cache._get_local_reco(reco_cache._local_reco_key(19, 2, "menu", "a", "")) is None


@pytest.mark.asyncio
async def test_reco_cache_invalidation_deletes_large_key_sets_in_bounded_batches():
    from app.services import reco_cache

    fake = FakeRedis()
    for i in range(450):
        fake.store[f"snh:reco:c77:s1:menu:b{i}"] = "{}"
    with (
        patch.object(reco_cache, "get_redis", _patch_get_redis(fake)),
        patch.object(reco_cache, "spawn_bg", lambda coro: coro.close()),
    ):
        await reco_cache.invalidate_customer(77)
    assert len(fake.delete_calls) == 450
    assert not any(key in fake.store for key in fake.delete_calls)


@pytest.mark.asyncio
async def test_owned_lock_release_does_not_delete_replaced_cooldown():
    from app.core.redis_client import release_owned_lock

    fake = FakeRedis()
    fake.store["snh:welcome:unknown:camera:5"] = "new-cooldown-owner"
    released = await release_owned_lock(fake, "snh:welcome:unknown:camera:5", "old-owner")
    assert released is False
    assert fake.store["snh:welcome:unknown:camera:5"] == "new-cooldown-owner"


@pytest.mark.asyncio
async def test_critical_scheduler_lock_is_not_granted_if_redis_client_fails():
    from app.services import scheduler_admin
    from app.core import redis_client

    async def failing_client():
        raise RuntimeError("connection unavailable")

    with patch.object(redis_client, "get_redis", failing_client):
        async with scheduler_admin.redis_job_lock("test-critical-job", ttl=30) as acquired:
            assert acquired is False


@pytest.mark.asyncio
async def test_scheduler_job_lock_renews_long_running_lease():
    from app.services import scheduler_admin
    from app.core import redis_client

    fake = FakeRedis()
    with patch.object(redis_client, "get_redis", _patch_get_redis(fake)):
        async with scheduler_admin.redis_job_lock("test-long-job", ttl=5) as acquired:
            assert acquired is True
            await asyncio.sleep(1.8)
    assert any("expire" in call[0].lower() for call in fake.eval_calls)
    assert any("del" in call[0].lower() for call in fake.eval_calls)


def test_production_redis_url_requires_password_for_redis_and_rediss():
    from app.core.startup_checks import _production_redis_url_errors

    assert any("密码" in x for x in _production_redis_url_errors("redis://user@cache.example.net:6379/0"))
    assert any("密码" in x for x in _production_redis_url_errors("rediss://cache.example.net:6379/0"))
    assert _production_redis_url_errors("rediss://:secret@cache.example.net:6379/0") == []


def test_production_redis_url_rejects_loopback_and_invalid_scheme():
    from app.core.startup_checks import _production_redis_url_errors

    assert any("回环" in x for x in _production_redis_url_errors("rediss://:secret@127.0.0.1:6379/0"))
    assert any("redis://" in x for x in _production_redis_url_errors("http://:secret@cache.example.net:6379/0"))


# ── 缓存重建等待：轮询缓存，不允许固定等待后并发回源 ──

@pytest.mark.asyncio
async def test_menu_rebuild_wait_polls_until_cache_is_ready():
    from app.core import menu_cache

    calls = {"n": 0}
    async def fake_get(_store_id):
        calls["n"] += 1
        return {"categories": []} if calls["n"] >= 3 else None

    with patch.object(menu_cache, "get_menu_bundle", fake_get):
        result = await menu_cache.wait_for_rebuild(8, timeout=0.5)
    assert result == {"categories": []}
    assert calls["n"] >= 3


@pytest.mark.asyncio
async def test_menu_rebuild_wait_is_bounded_when_cache_never_appears():
    from app.core import menu_cache

    calls = {"n": 0}
    async def fake_get(_store_id):
        calls["n"] += 1
        return None

    with patch.object(menu_cache, "get_menu_bundle", fake_get):
        result = await menu_cache.wait_for_rebuild(8, timeout=0.05)
    assert result is None
    assert calls["n"] >= 1


@pytest.mark.asyncio
async def test_reco_rebuild_wait_polls_until_cache_is_ready():
    from app.services import reco_cache

    calls = {"n": 0}
    async def fake_get(*_args):
        calls["n"] += 1
        return {"items": []} if calls["n"] >= 3 else None

    with patch.object(reco_cache, "get_cached_reco", fake_get):
        result = await reco_cache.wait_for_reco(1, 2, "menu", "a", timeout=0.5)
    assert result == {"items": []}
    assert calls["n"] >= 3


@pytest.mark.asyncio
async def test_reco_rebuild_wait_is_bounded_when_cache_never_appears():
    from app.services import reco_cache

    calls = {"n": 0}
    async def fake_get(*_args):
        calls["n"] += 1
        return None

    with patch.object(reco_cache, "get_cached_reco", fake_get):
        result = await reco_cache.wait_for_reco(1, 2, "menu", "a", timeout=0.05)
    assert result is None
    assert calls["n"] >= 1



@pytest.mark.asyncio
async def test_redis_owned_lock_heartbeat_renews_and_stops():
    from app.core.redis_client import start_redis_lock_heartbeat, stop_redis_lock_heartbeat

    fake = FakeRedis()
    fake.store["lease:key"] = "owner-a"
    task = start_redis_lock_heartbeat(
        fake, "lease:key", "owner-a", ttl=3, label="test"
    )
    await asyncio.sleep(1.1)
    assert any("expire" in call[0].lower() for call in fake.eval_calls)
    await stop_redis_lock_heartbeat(task)
    assert task.done()


@pytest.mark.asyncio
async def test_redis_owned_lock_heartbeat_stops_after_owner_changes():
    from app.core.redis_client import start_redis_lock_heartbeat

    fake = FakeRedis()
    fake.store["lease:key"] = "owner-a"
    task = start_redis_lock_heartbeat(
        fake, "lease:key", "owner-a", ttl=3, label="test-lost"
    )
    fake.store["lease:key"] = "owner-b"
    await asyncio.sleep(1.1)
    assert task.done()
    assert fake.store["lease:key"] == "owner-b"



@pytest.mark.asyncio
async def test_redis_lock_heartbeat_stops_when_owner_task_exits():
    from app.core.redis_client import start_redis_lock_heartbeat

    fake = FakeRedis()
    fake.store["lease:orphan"] = "owner-orphan"

    async def acquire_and_exit():
        return start_redis_lock_heartbeat(
            fake, "lease:orphan", "owner-orphan", ttl=3, label="orphan-test"
        )

    heartbeat = await asyncio.create_task(acquire_and_exit())
    await asyncio.sleep(1.1)
    assert heartbeat.done()
    assert fake.eval_calls == []

@pytest.mark.asyncio
async def test_redis_client_uses_bounded_blocking_pool(monkeypatch):
    """连接池耗尽时有限等待，不应立即拒绝或无限排队。"""
    from types import SimpleNamespace
    from app.core import redis_client

    pool = MagicMock()
    pool.disconnect = AsyncMock()
    client = MagicMock()
    client.aclose = AsyncMock()

    redis_client._redis = None
    redis_client._redis_pool = None
    monkeypatch.setattr(
        redis_client, "get_settings",
        lambda: SimpleNamespace(redis_url="redis://cache.example:6379/0", redis_max_connections=23),
    )
    with (
        patch.object(
            redis_client.redis.BlockingConnectionPool, "from_url", return_value=pool
        ) as make_pool,
        patch.object(redis_client.redis, "Redis", return_value=client) as make_client,
    ):
        assert await redis_client.get_redis() is client
        make_pool.assert_called_once()
        kwargs = make_pool.call_args.kwargs
        assert kwargs["max_connections"] == 23
        assert kwargs["timeout"] == redis_client._REDIS_POOL_WAIT_TIMEOUT_SEC
        assert 0 < kwargs["timeout"] <= 1.0
        make_client.assert_called_once_with(connection_pool=pool)
        await redis_client.close_redis()

    client.aclose.assert_awaited_once()
    pool.disconnect.assert_awaited_once_with(inuse_connections=True)
    assert redis_client._redis is None
    assert redis_client._redis_pool is None


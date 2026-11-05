"""Redis 加固修复验证测试：覆盖 jitter/互斥锁/空值标记/延迟双删/大Key/TTL补全等。"""
from __future__ import annotations

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
        # 模拟项目中使用的几类 Lua 原子脚本，避免把 compare-expire 错当成 delete。
        if "INCR" in script:
            count = int(self.store.get(key, 0)) + 1
            self.store[key] = str(count)
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


# ── reco_cache jitter TTL ──

@pytest.mark.asyncio
async def test_reco_cache_set_uses_jitter():
    from app.services import reco_cache
    fake = FakeRedis()
    with patch.object(reco_cache, "get_redis", _patch_get_redis(fake)):
        await reco_cache.set_cached_reco(1, 1, "default", "a", {"items": []}, "")
        key, value, nx, ex = fake.set_calls[-1]
        # TTL 应在 96-144 范围内（120 ±20%）
        assert ex is not None
        assert 96 <= ex <= 144


# ── reco_cache invalidate_customer 延迟双删 ──

@pytest.mark.asyncio
async def test_reco_cache_invalidate_uses_retry(monkeypatch):
    from app.services import reco_cache
    fake = FakeRedis()
    # 预填充一些推荐缓存键
    fake.store["snh:reco:c1:s1:default:a"] = "{}"
    with patch.object(reco_cache, "get_redis", _patch_get_redis(fake)):
        # 禁用延迟双删的 sleep
        monkeypatch.setattr("asyncio.sleep", AsyncMock())
        await reco_cache.invalidate_customer(1)
        # 应该调用了 delete
        assert len(fake.delete_calls) > 0


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
async def test_reco_rebuild_lock_old_owner_cannot_delete_new_owner():
    from app.services import reco_cache

    fake = FakeRedis()
    key = "snh:reco:lock:c7:s5:default:b0"
    fake.store[key] = "new-owner-token"
    with patch.object(reco_cache, "get_redis", _patch_get_redis(fake)):
        await reco_cache.release_reco_lock(7, 5, "default", "b0", token="expired-old-owner-token")
    assert fake.store[key] == "new-owner-token"


def test_rate_limit_bucket_collapses_dynamic_ids_and_keeps_sensitive_policies_separate():
    from app.core.rate_limit import _rate_limit_bucket

    assert _rate_limit_bucket("/api/orders/10001") == _rate_limit_bucket("/api/orders/99999")
    assert _rate_limit_bucket("/api/miniprogram/orders/10001") == "/api/miniprogram"
    assert _rate_limit_bucket("/api/auth/login") == "/api/auth/login"
    assert _rate_limit_bucket("/api/auth/verify-password") == "/api/auth/verify-password"

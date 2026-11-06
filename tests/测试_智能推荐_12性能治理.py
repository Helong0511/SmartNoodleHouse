"""Task 12：性能治理 —— GZip、性能指标滚动窗口、菜单版本戳 304 语义。"""
from __future__ import annotations

import gzip
import logging

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from httpx import ASGITransport, AsyncClient

from app.api.miniprogram import get_menu_bundle_api
from app.core import menu_cache, perf_metrics
from app.core.performance_middleware import JsonGZipMiddleware, PerformanceMetricsMiddleware
from app.models import StoreProductStock
from app.services.recommendation_service import invalidate_store_caches
from tests.reco_factories import make_category, make_product, make_store


async def _asgi_get(app, path: str, headers: dict[str, str] | None = None):
    """绕过 httpx 自动解压，直接读 ASGI 响应头（httpx 0.28 会剥掉 content-encoding）。"""
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": hdrs,
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    resp_headers = {k.decode().lower(): v.decode() for k, v in start.get("headers", [])}
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return int(start["status"]), resp_headers, body


def _async_client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class FakePipeline:
    def __init__(self, fake: "FakeRedis"):
        self.fake = fake
        self.ops: list = []

    def __getattr__(self, name):
        def _buf(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self
        return _buf

    async def execute(self):
        out = []
        for name, args, kwargs in self.ops:
            out.append(await getattr(self.fake, name)(*args, **kwargs))
        return out


class FakeRedis:
    """最小内存 Redis 替身：string/list/set + pipeline，TTL 忽略。"""

    def __init__(self):
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list] = {}
        self.sets: dict[str, set] = {}

    def pipeline(self):
        return FakePipeline(self)

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None, nx=False, xx=False, px=None):
        if nx and key in self.kv:
            return None
        if xx and key not in self.kv:
            return None
        self.kv[key] = str(value)
        return True

    async def eval(self, script, numkeys, *args):
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        # owner-token 心跳续租
        if "redis.call('expire'" in script and len(keys) == 1:
            if self.kv.get(keys[0]) == str(argv[0]):
                return await self.expire(keys[0], int(argv[1]))
            return 0
        # owner-token 释放
        if "redis.call('del'" in script and len(keys) == 1:
            if self.kv.get(keys[0]) == str(argv[0]):
                return await self.delete(keys[0])
            return 0
        # 菜单 bundle + version 原子读取。
        if "MENU_GET_BUNDLE_WITH_VERSION" in script:
            bundle = self.kv.get(keys[0])
            version = self.kv.get(keys[1])
            return [bundle, version] if bundle is not None and version is not None else None
        # 菜单缓存发布：同时校验锁 token 与构建前版本，模拟 Lua 原子比较。
        if "redis.call('SET', KEYS[2]" in script and len(keys) == 3:
            lock_key, bundle_key, version_key = keys
            token, raw, _ttl, expected_version = argv
            if (
                self.kv.get(lock_key) == str(token)
                and self.kv.get(version_key, "0") == str(expected_version)
            ):
                self.kv[bundle_key] = str(raw)
                return "OK"
            return None
        raise NotImplementedError("FakeRedis.eval 未覆盖该 Lua 脚本")

    async def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.kv:
                del self.kv[k]
                n += 1
        return n

    async def incr(self, key, amount=1):
        val = int(self.kv.get(key, 0)) + int(amount)
        self.kv[key] = str(val)
        return val

    async def expire(self, key, seconds):
        return 1

    async def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def rpush(self, key, *values):
        self.lists.setdefault(key, []).extend(str(v) for v in values)
        return len(self.lists[key])

    async def ltrim(self, key, start, end):
        lst = self.lists.get(key)
        if lst is None:
            return
        if start < 0 and end < 0:
            keep = lst[max(0, len(lst) + start): len(lst) + end + 1]
        elif end < 0:
            keep = lst[start: len(lst) + end + 1]
        else:
            keep = lst[start:end + 1]
        self.lists[key] = keep

    async def lrange(self, key, start, end):
        lst = self.lists.get(key, [])
        if end == -1:
            return list(lst[start:])
        return list(lst[start:end + 1])


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr(menu_cache, "get_redis", _get_redis)
    monkeypatch.setattr(perf_metrics, "get_redis", _get_redis)
    return fake


# ---------- GZip ----------

def _gzip_app():
    app = FastAPI()
    app.add_middleware(JsonGZipMiddleware)

    @app.get("/big")
    async def big():
        return {"text": "面" * 900}

    @app.get("/small")
    async def small():
        return {"a": 1}

    @app.get("/text", response_class=PlainTextResponse)
    async def text_resp():
        return "x" * 4000

    return app


@pytest.mark.asyncio
async def test_gzip_compresses_large_json_and_roundtrip():
    app = _gzip_app()
    status, headers, raw = await _asgi_get(
        app, "/big", headers={"accept-encoding": "gzip"}
    )
    assert status == 200
    assert headers.get("content-encoding") == "gzip"
    assert "accept-encoding" in headers.get("vary", "").lower()
    body = gzip.decompress(raw).decode("utf-8")
    assert '"text"' in body
    assert body.count("面") == 900

    async with _async_client(app) as client:
        r = await client.get("/big", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200
        assert len(r.json()["text"]) == 900


@pytest.mark.asyncio
async def test_gzip_skips_small_json_and_non_json():
    app = _gzip_app()
    _, small_h, _ = await _asgi_get(app, "/small", headers={"accept-encoding": "gzip"})
    assert small_h.get("content-encoding") is None

    _, text_h, text_body = await _asgi_get(app, "/text", headers={"accept-encoding": "gzip"})
    assert text_h.get("content-encoding") is None
    assert len(text_body) == 4000

    _, plain_h, _ = await _asgi_get(app, "/big", headers={"accept-encoding": "identity"})
    assert plain_h.get("content-encoding") is None

    async with _async_client(app) as client:
        small = await client.get("/small", headers={"Accept-Encoding": "gzip"})
        assert small.json() == {"a": 1}


# ---------- 性能指标 ----------

@pytest.mark.asyncio
async def test_perf_metrics_percentiles_and_qps(fake_redis):
    for ms in range(10, 101):  # 10..100 共 91 个样本
        await perf_metrics.record_request_timing("/api/miniprogram/menu", float(ms))
    report = await perf_metrics.query_perf_metrics(window_hours=1)
    row = report["routes"]["/api/miniprogram/menu"]
    assert row["count"] == 91
    assert row["sample_size"] == 91
    assert 54 <= row["p50_ms"] <= 56
    assert 94 <= row["p95_ms"] <= 97
    assert 98 <= row["p99_ms"] <= 100
    assert row["max_ms"] == 100
    assert row["qps"] == round(91 / 3600, 4)

    empty = await perf_metrics.query_perf_metrics(window_hours=1, route="/not/exist")
    assert empty["routes"] == {}


def test_normalize_route():
    assert perf_metrics.normalize_route("/api/orders/123", None) == "/api/orders/:id"
    assert perf_metrics.normalize_route(
        "/x", "/api/orders/{order_id}/items/{item_id}"
    ) == "/api/orders/:id/items/:id"


@pytest.mark.asyncio
async def test_slow_request_logged(fake_redis, caplog):
    import asyncio

    app = FastAPI()
    app.add_middleware(PerformanceMetricsMiddleware)

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(0.85)
        return {"ok": True}

    caplog.set_level(logging.WARNING, logger="app.core.performance_middleware")
    async with _async_client(app) as client:
        r = await client.get("/slow")
        assert r.status_code == 200
    assert any("slow_request" in rec.message and "/slow" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_metrics_endpoint_reads_middleware_data(fake_redis):
    """真实中间件记录 -> 管理接口能读到数据（TR-12.2 接口有数据）。"""
    from app.main import app as real_app
    from app.api.performance import reco_metrics

    async with _async_client(real_app) as client:
        # /health 被中间件跳过；菜单接口缺 store_id 返回 400，但计时在 finally 中仍会落桶
        await client.get("/api/miniprogram/menu")

    body = await reco_metrics(request=None, window_hours=1)
    routes = body["data"]["routes"]
    # FastAPI include_router 已将 /api prefix 纳入最终 route.path，指标键应与实际请求路径一致。
    assert "/miniprogram/menu" in routes
    assert routes["/miniprogram/menu"]["count"] >= 1


# ---------- 菜单聚合版本戳 ----------

@pytest.mark.asyncio
async def test_menu_bundle_publish_is_fenced_by_build_version(fake_redis):
    from app.core.cache_keys import menu_bundle_key, menu_rebuild_lock_key, menu_version_key

    store_id = 99123
    await fake_redis.set(menu_rebuild_lock_key(store_id), "owner-token")
    await fake_redis.set(menu_version_key(store_id), "7")

    published = await menu_cache.set_menu_bundle(
        store_id,
        {"version": 7, "products": {"items": []}},
        owner_token="owner-token",
        expected_version=7,
    )
    assert published is True
    assert menu_bundle_key(store_id) in fake_redis.kv

    # 写路径在快照构建后 bump 版本：旧 worker 即使仍持有租约，也不得覆盖新版本。
    await fake_redis.set(menu_version_key(store_id), "8")
    stale_publish = await menu_cache.set_menu_bundle_if_owner(
        store_id,
        {"version": 7, "products": {"items": ["stale"]}},
        "owner-token",
        expected_version=7,
    )
    assert stale_publish is False
    assert '"stale"' not in fake_redis.kv[menu_bundle_key(store_id)]


@pytest.mark.asyncio
async def test_menu_bundle_version_flow(fake_redis, db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p1 = await make_product(db, store.id, cat.id, name="红烧牛肉面")
    p2 = await make_product(db, store.id, cat.id, name="青菜")
    # 本店停售品必须被聚合剔除
    db.add(StoreProductStock(id=910001, store_id=store.id, product_id=p2.id, status=0))
    await db.flush()

    first = await get_menu_bundle_api(request=None, db=db, store_id=store.id)
    assert first["code"] == 0
    d1 = first["data"]
    assert d1["changed"] is True
    assert d1["version"] == 1
    assert len(d1["categories"]) == 1
    assert [it["id"] for it in d1["products"]["items"]] == [p1.id]
    assert d1["products"]["items"][0]["has_options"] is False

    # 携带相同版本：changed:false 小响应（可附带 visibility_day 等元数据）
    fresh = await get_menu_bundle_api(request=None, db=db, store_id=store.id, version=1)
    assert fresh["data"]["changed"] is False
    assert fresh["data"]["version"] == 1

    # 餐品写路径失效：版本递增，客户端旧版本重新拿全量
    await invalidate_store_caches(store.id)
    changed = await get_menu_bundle_api(request=None, db=db, store_id=store.id, version=1)
    assert changed["data"]["changed"] is True
    assert changed["data"]["version"] == 2
    assert len(changed["data"]["products"]["items"]) == 1


@pytest.mark.asyncio
async def test_menu_bundle_invalidates_stale_local_cache_from_remote_version(fake_redis, db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    product = await make_product(db, store.id, cat.id, name="旧菜单名")

    first = await get_menu_bundle_api(request=None, db=db, store_id=store.id)
    old_version = first["data"]["version"]
    assert first["data"]["products"]["items"][0]["name"] == "旧菜单名"

    # 模拟另一个 worker 更新数据并只 bump Redis 版本；本 worker 的进程内缓存没有被直接清除。
    product.name = "新菜单名"
    await db.flush()
    new_version = await menu_cache.bump_menu_version(store.id)
    assert new_version > old_version

    refreshed = await get_menu_bundle_api(
        request=None, db=db, store_id=store.id, version=old_version
    )
    assert refreshed["data"]["changed"] is True
    assert refreshed["data"]["version"] == new_version
    assert refreshed["data"]["products"]["items"][0]["name"] == "新菜单名"


@pytest.mark.asyncio
async def test_menu_bundle_requires_store(fake_redis, db_session):
    from app.core.exceptions import AppException

    with pytest.raises(AppException):
        await get_menu_bundle_api(request=None, db=db_session, store_id=0)


@pytest.mark.asyncio
async def test_menu_bundle_invalidates_on_category_schedule_change(fake_redis, db_session):
    """后台改分类时段排序后：版本立刻抬升，小程序旧 version 必须拿到新顺序。"""
    from datetime import time

    from sqlalchemy import select

    from app.models import CategorySortSchedule, CategorySortScheduleItem
    from tests.reco_factories import _SCHEDULE_ID, _SCHEDULE_ITEM_ID

    db = db_session
    store = await make_store(db)
    c_early = await make_category(db, store.id, name="早点", sort_order=0)
    c_noodle = await make_category(db, store.id, name="面", sort_order=1)
    await make_product(db, store.id, c_early.id, name="豆浆")
    await make_product(db, store.id, c_noodle.id, name="牛肉面")

    sched = CategorySortSchedule(
        id=next(_SCHEDULE_ID),
        store_id=store.id,
        name="午市",
        start_time=time(0, 0),
        end_time=time(0, 0),  # 全天
        priority=10,
        status=1,
    )
    db.add(sched)
    await db.flush()
    db.add_all(
        [
            CategorySortScheduleItem(
                id=next(_SCHEDULE_ITEM_ID),
                schedule_id=sched.id,
                category_id=c_noodle.id,
                sort_order=0,
            ),
            CategorySortScheduleItem(
                id=next(_SCHEDULE_ITEM_ID),
                schedule_id=sched.id,
                category_id=c_early.id,
                sort_order=1,
            ),
        ]
    )
    await db.flush()

    first = await get_menu_bundle_api(request=None, db=db, store_id=store.id)
    assert first["data"]["changed"] is True
    v1 = first["data"]["version"]
    assert [c["id"] for c in first["data"]["categories"]] == [c_noodle.id, c_early.id]

    # 调换时段内分类顺序（模拟后台保存），并走与写接口相同的失效路径
    items = (
        await db.execute(
            select(CategorySortScheduleItem).where(CategorySortScheduleItem.schedule_id == sched.id)
        )
    ).scalars().all()
    for it in items:
        it.sort_order = 0 if it.category_id == c_early.id else 1
    await db.flush()
    await invalidate_store_caches(store.id)

    same_ver = await get_menu_bundle_api(request=None, db=db, store_id=store.id, version=v1)
    assert same_ver["data"]["changed"] is True
    assert same_ver["data"]["version"] > v1
    assert [c["id"] for c in same_ver["data"]["categories"]] == [c_early.id, c_noodle.id]


@pytest.mark.asyncio
async def test_menu_bundle_bumps_when_active_schedule_window_changes(fake_redis, db_session):
    """跨时段窗口：缓存里的 active_schedule_id 与当前不一致时必须 bump，避免一直 changed:false。"""
    from datetime import datetime, time
    from unittest.mock import patch

    from app.models import CategorySortSchedule, CategorySortScheduleItem
    from tests.reco_factories import _SCHEDULE_ID, _SCHEDULE_ITEM_ID

    db = db_session
    store = await make_store(db)
    c_a = await make_category(db, store.id, name="A", sort_order=0)
    c_b = await make_category(db, store.id, name="B", sort_order=1)
    await make_product(db, store.id, c_a.id, name="A品")
    await make_product(db, store.id, c_b.id, name="B品")

    lunch = CategorySortSchedule(
        id=next(_SCHEDULE_ID),
        store_id=store.id,
        name="午市",
        start_time=time(10, 0),
        end_time=time(14, 0),
        priority=5,
        status=1,
    )
    dinner = CategorySortSchedule(
        id=next(_SCHEDULE_ID),
        store_id=store.id,
        name="晚市",
        start_time=time(17, 0),
        end_time=time(21, 0),
        priority=5,
        status=1,
    )
    db.add_all([lunch, dinner])
    await db.flush()
    db.add_all(
        [
            CategorySortScheduleItem(
                id=next(_SCHEDULE_ITEM_ID),
                schedule_id=lunch.id,
                category_id=c_a.id,
                sort_order=0,
            ),
            CategorySortScheduleItem(
                id=next(_SCHEDULE_ITEM_ID),
                schedule_id=lunch.id,
                category_id=c_b.id,
                sort_order=1,
            ),
            CategorySortScheduleItem(
                id=next(_SCHEDULE_ITEM_ID),
                schedule_id=dinner.id,
                category_id=c_b.id,
                sort_order=0,
            ),
            CategorySortScheduleItem(
                id=next(_SCHEDULE_ITEM_ID),
                schedule_id=dinner.id,
                category_id=c_a.id,
                sort_order=1,
            ),
        ]
    )
    await db.flush()

    with (
        patch("app.services.menu_service.now_local") as menu_now,
        patch("app.services.category_schedule.now_local") as sched_now,
    ):
        menu_now.return_value = datetime(2026, 9, 24, 11, 0, 0)
        sched_now.return_value = datetime(2026, 9, 24, 11, 0, 0)
        lunch_resp = await get_menu_bundle_api(request=None, db=db, store_id=store.id)
        v_lunch = lunch_resp["data"]["version"]
        assert [c["id"] for c in lunch_resp["data"]["categories"]] == [c_a.id, c_b.id]

        # 仍用午市 version 请求，但时钟已到晚市：必须 changed + 新序
        menu_now.return_value = datetime(2026, 9, 24, 18, 0, 0)
        sched_now.return_value = datetime(2026, 9, 24, 18, 0, 0)
        dinner_resp = await get_menu_bundle_api(
            request=None, db=db, store_id=store.id, version=v_lunch
        )
        assert dinner_resp["data"]["changed"] is True
        assert dinner_resp["data"]["version"] > v_lunch
        assert [c["id"] for c in dinner_resp["data"]["categories"]] == [c_b.id, c_a.id]

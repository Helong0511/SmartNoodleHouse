"""Task 12：性能治理 —— GZip、性能指标滚动窗口、菜单版本戳 304 语义。"""
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from starlette.testclient import TestClient

from app.api.v1.miniprogram import get_menu_bundle_api
from app.core import menu_cache, perf_metrics
from app.core.performance_middleware import JsonGZipMiddleware, PerformanceMetricsMiddleware
from app.models import StoreProductStock
from app.services.recommendation_service import invalidate_store_caches
from tests.reco_factories import make_category, make_product, make_store


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

    async def set(self, key, value, ex=None):
        self.kv[key] = str(value)

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


def test_gzip_compresses_large_json_and_roundtrip():
    client = TestClient(_gzip_app())
    r = client.get("/big", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert "Accept-Encoding" in r.headers.get("vary", "")
    # TestClient 自动解压，业务内容一致
    body = r.json()
    assert len(body["text"]) == 900


def test_gzip_skips_small_json_and_non_json():
    client = TestClient(_gzip_app())
    small = client.get("/small", headers={"Accept-Encoding": "gzip"})
    assert small.headers.get("content-encoding") is None
    assert small.json() == {"a": 1}

    text = client.get("/text", headers={"Accept-Encoding": "gzip"})
    assert text.headers.get("content-encoding") is None
    assert len(text.text) == 4000

    no_accept = client.get("/big", headers={"Accept-Encoding": "identity"})
    assert no_accept.headers.get("content-encoding") is None


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


def test_slow_request_logged(fake_redis, caplog):
    app = FastAPI()
    app.add_middleware(PerformanceMetricsMiddleware)

    @app.get("/slow")
    async def slow():
        import asyncio
        await asyncio.sleep(0.85)
        return {"ok": True}

    caplog.set_level(logging.WARNING, logger="app.core.performance_middleware")
    client = TestClient(app)
    r = client.get("/slow")
    assert r.status_code == 200
    assert any("slow_request" in rec.message and "/slow" in rec.message for rec in caplog.records)


def test_metrics_endpoint_reads_middleware_data(fake_redis):
    """真实中间件记录 -> 管理接口能读到数据（TR-12.2 接口有数据）。"""
    from app.main import app as real_app
    from app.api.v1.performance import reco_metrics

    client = TestClient(real_app)
    # /health 被中间件跳过；菜单接口缺 store_id 返回 400，但计时在 finally 中仍会落桶
    client.get("/api/miniprogram/menu")

    import asyncio
    body = asyncio.get_event_loop().run_until_complete(
        reco_metrics(request=None, window_hours=1)
    )
    routes = body["data"]["routes"]
    # route.path 不含 /api 外层 prefix，键为路由模板 /miniprogram/menu
    assert "/miniprogram/menu" in routes
    assert routes["/miniprogram/menu"]["count"] >= 1


# ---------- 菜单聚合版本戳 ----------

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

    # 携带相同版本：changed:false 小响应
    fresh = await get_menu_bundle_api(request=None, db=db, store_id=store.id, version=1)
    assert fresh["data"] == {"changed": False, "version": 1}

    # 商品写路径失效：版本递增，客户端旧版本重新拿全量
    await invalidate_store_caches(store.id)
    changed = await get_menu_bundle_api(request=None, db=db, store_id=store.id, version=1)
    assert changed["data"]["changed"] is True
    assert changed["data"]["version"] == 2
    assert len(changed["data"]["products"]["items"]) == 1


@pytest.mark.asyncio
async def test_menu_bundle_requires_store(fake_redis, db_session):
    from app.core.exceptions import AppException

    with pytest.raises(AppException):
        await get_menu_bundle_api(request=None, db=db_session, store_id=0)

"""智能推荐 / 菜单聚合接口性能基准（TR-12.1 硬性断言）。

独立运行（非 pytest 用例）：
    python tests/推荐系统性能基准.py

构造同机构 500 SKU / 10 分类，对比两条路径的连续耗时：
  A. 直查基线：build_menu_bundle 每次直查数据库组装；
  B. 缓存路径：get_or_build_menu_bundle 命中 Redis 缓存（含 JSON 反序列化）。
另测冷构建（进程缓存与 bundle 均未命中、构建后回写）的 P95。

断言（任一不满足以非 0 退出）：
  - 缓存命中 P95 <= 100ms；
  - 冷/预热构建 P95 <= 500ms；
  - 缓存命中平均耗时较直查基线下降 >= 40%。
"""
from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
from decimal import Decimal

# 复用测试工厂（显式 id，SQLite BIGINT 不自增）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core import menu_cache
from app.core.database import Base
from app.services.menu_service import (
    build_menu_bundle,
    get_or_build_menu_bundle,
    invalidate_local_menu_cache,
)
from tests.reco_factories import make_category, make_product, make_store

N_CATEGORIES = 10
N_PRODUCTS = 500
BASELINE_RUNS = 20
CACHE_RUNS = 20
COLD_RUNS = 5

HIT_P95_LIMIT_MS = 100.0
COLD_P95_LIMIT_MS = 500.0
AVG_IMPROVEMENT = 0.40


class FakeRedis:
    """最小内存 Redis 替身（string 即可，TTL 忽略）。"""

    def __init__(self):
        self.kv: dict[str, str] = {}

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


def percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def stats_block(name: str, samples: list[float]) -> dict[str, float]:
    return {
        "name": name,
        "avg": statistics.mean(samples) * 1000,
        "p50": percentile(samples, 0.50) * 1000,
        "p95": percentile(samples, 0.95) * 1000,
        "max": max(samples) * 1000,
    }


async def seed_data(db: AsyncSession) -> int:
    store = await make_store(db, name="基准大店")
    cats = []
    per = N_PRODUCTS // N_CATEGORIES
    for ci in range(N_CATEGORIES):
        cats.append(await make_category(db, store.id, name=f"分类{ci + 1:02d}"))
    for i in range(N_PRODUCTS):
        cat = cats[i % N_CATEGORIES]
        await make_product(
            db,
            store.id,
            cat.id,
            name=f"基准面食{i + 1:04d}",
            price=str(Decimal("10.00") + (i % 40)),
            sales_count=(i * 7) % 500,
            is_hot=(i % 13 == 0),
            is_recommended=(i % 17 == 0),
        )
    await db.commit()
    return store.id


async def timed(coro_factory, runs: int) -> list[float]:
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        await coro_factory()
        samples.append(time.perf_counter() - t0)
    return samples


async def main() -> int:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    fake = FakeRedis()

    async def _get_redis():
        return fake

    menu_cache.get_redis = _get_redis

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with SessionLocal() as db:
            sid = await seed_data(db)

            # 正确性自检
            bundle = await build_menu_bundle(db, sid)
            assert len(bundle["categories"]) == N_CATEGORIES, "分类数不符"
            assert bundle["products"]["total"] == N_PRODUCTS, "餐品数不符"

            # A. 直查基线（不走任何缓存）
            baseline = await timed(lambda: build_menu_bundle(db, sid), BASELINE_RUNS)

            # B. 缓存路径：预热后连续命中
            await get_or_build_menu_bundle(db, sid)
            cached = await timed(lambda: get_or_build_menu_bundle(db, sid), CACHE_RUNS)

            # C. 冷/预热构建：进程缓存与 bundle 双未命中，构建后回写
            cold = []
            bundle_key = menu_cache.menu_bundle_key(sid)
            for _ in range(COLD_RUNS):
                invalidate_local_menu_cache(sid)
                await fake.delete(bundle_key)
                t0 = time.perf_counter()
                await get_or_build_menu_bundle(db, sid)
                cold.append(time.perf_counter() - t0)
    finally:
        await engine.dispose()

    base_s = stats_block(f"直查基线 x{BASELINE_RUNS}", baseline)
    hit_s = stats_block(f"Redis缓存命中 x{CACHE_RUNS}", cached)
    cold_s = stats_block(f"冷构建回写 x{COLD_RUNS}", cold)
    improvement = (base_s["avg"] - hit_s["avg"]) / base_s["avg"] if base_s["avg"] else 0.0

    print("=" * 72)
    print(f"菜单聚合性能基准：{N_CATEGORIES} 分类 / {N_PRODUCTS} SKU（内存 SQLite）")
    print("-" * 72)
    for s in (base_s, hit_s, cold_s):
        print(
            f"{s['name']:<22} avg={s['avg']:8.2f}ms  p50={s['p50']:8.2f}ms  "
            f"p95={s['p95']:8.2f}ms  max={s['max']:8.2f}ms"
        )
    print("-" * 72)
    print(f"缓存命中平均耗时下降：{improvement * 100:.1f}%（门槛 {AVG_IMPROVEMENT * 100:.0f}%）")
    print("=" * 72)

    failures = []
    if hit_s["p95"] > HIT_P95_LIMIT_MS:
        failures.append(f"缓存命中 P95 {hit_s['p95']:.2f}ms > {HIT_P95_LIMIT_MS:.0f}ms")
    if cold_s["p95"] > COLD_P95_LIMIT_MS:
        failures.append(f"冷构建 P95 {cold_s['p95']:.2f}ms > {COLD_P95_LIMIT_MS:.0f}ms")
    if improvement < AVG_IMPROVEMENT:
        failures.append(f"平均耗时下降 {improvement * 100:.1f}% < {AVG_IMPROVEMENT * 100:.0f}%")

    if failures:
        print("基准未达标：")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("基准全部达标。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""请求性能指标（Task 12）：按路由模板 × 分钟桶写 Redis 滚动窗口。

- 样本：list 保留每桶最近 SAMPLES_PER_BUCKET 个耗时（ms 整数），用于 P50/P95/P99；
- 计数：独立 counter，保证 QPS 与平均值不被样本截断影响；
- 桶 TTL 25 小时，查询默认聚合近 24 小时；
- Redis 全部异常静默，绝不影响请求主路径。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from app.core.redis_client import get_redis

WINDOW_HOURS = 24
BUCKET_TTL_SECONDS = 25 * 3600
SAMPLES_PER_BUCKET = 240
SLOW_REQUEST_MS = 800.0

_NUM_SEG = re.compile(r"/\d+(?=/|$)")
_UUID_SEG = re.compile(r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?=/|$)")
_HEX_SEG = re.compile(r"/[0-9a-fA-F]{16,}(?=/|$)")


def normalize_route(path: str, route_template: str | None = None) -> str:
    """路由模板归一化：优先用框架匹配模板（/orders/{id}），否则把数字/UUID 段替成 :id。"""
    raw = (route_template or path or "").strip()
    if not raw:
        return "unknown"
    if "{" in raw:
        # /api/v1/orders/{order_id} → /api/v1/orders/:id
        raw = re.sub(r"\{[^}/]+\}", ":id", raw)
    raw = _UUID_SEG.sub("/:id", raw)
    raw = _HEX_SEG.sub("/:id", raw)
    raw = _NUM_SEG.sub("/:id", raw)
    return raw or "unknown"


def _minute_bucket(epoch: float | None = None) -> int:
    return int((epoch or time.time()) // 60)


def _sample_key(bucket: int, route: str) -> str:
    return f"snh:perfs:{bucket}:{route}"


def _count_key(bucket: int, route: str) -> str:
    return f"snh:perfc:{bucket}:{route}"


async def record_request_timing(route: str, duration_ms: float, *, now: float | None = None) -> None:
    """记录单次请求耗时（样本 + 计数）。"""
    bucket = _minute_bucket(now)
    ms = max(0, int(round(duration_ms)))
    try:
        r = await get_redis()
        pipe = r.pipeline()
        pipe.sadd("snh:perf:routes", route)
        sk = _sample_key(bucket, route)
        pipe.rpush(sk, ms)
        pipe.ltrim(sk, -SAMPLES_PER_BUCKET, -1)
        pipe.expire(sk, BUCKET_TTL_SECONDS)
        ck = _count_key(bucket, route)
        pipe.incr(ck, 1)
        pipe.expire(ck, BUCKET_TTL_SECONDS)
        await pipe.execute()
    except Exception:
        pass


def _percentile(sorted_samples: list[int], pct: float) -> float:
    if not sorted_samples:
        return 0.0
    if len(sorted_samples) == 1:
        return float(sorted_samples[0])
    rank = (len(sorted_samples) - 1) * pct
    lo = int(rank)
    hi = min(lo + 1, len(sorted_samples) - 1)
    frac = rank - lo
    return sorted_samples[lo] * (1 - frac) + sorted_samples[hi] * frac


async def query_perf_metrics(
    *, window_hours: int = WINDOW_HOURS, route: str | None = None
) -> dict[str, Any]:
    """聚合近 window_hours 小时指标。返回 routes 映射与生成时间。"""
    hours = max(1, min(int(window_hours or WINDOW_HOURS), 48))
    now_bucket = _minute_bucket()
    buckets = [now_bucket - i for i in range(hours * 60)]
    try:
        r = await get_redis()
        if route:
            routes = [route]
        else:
            routes = list(await r.smembers("snh:perf:routes"))
        if not routes:
            return {
                "generated_at": int(time.time()),
                "window_hours": hours,
                "routes": {},
            }
        pipe = r.pipeline()
        for rt in routes:
            for b in buckets:
                pipe.lrange(_sample_key(b, rt), 0, -1)
                pipe.get(_count_key(b, rt))
        flat = await pipe.execute()
    except Exception:
        return {
            "generated_at": int(time.time()),
            "window_hours": hours,
            "routes": {},
            "note": "metrics_unavailable",
        }

    agg: dict[str, dict[str, Any]] = {}
    idx = 0
    for rt in routes:
        samples: list[int] = []
        count = 0
        for _ in buckets:
            raw_samples = flat[idx] or []
            raw_count = flat[idx + 1]
            idx += 2
            samples.extend(int(x) for x in raw_samples)
            count += int(raw_count or 0)
        if count <= 0:
            continue
        samples.sort()
        sample_total = sum(samples)
        window_seconds = hours * 3600
        agg[rt] = {
            "count": count,
            "qps": round(count / window_seconds, 4),
            "avg_ms": round(sample_total / len(samples), 2) if samples else None,
            "p50_ms": round(_percentile(samples, 0.50), 2),
            "p95_ms": round(_percentile(samples, 0.95), 2),
            "p99_ms": round(_percentile(samples, 0.99), 2),
            "max_ms": samples[-1] if samples else None,
            "sample_size": len(samples),
        }

    return {
        "generated_at": int(time.time()),
        "window_hours": hours,
        "routes": dict(sorted(agg.items(), key=lambda kv: kv[1]["count"], reverse=True)),
    }

"""P1-R5：核心链路轻量压测（下单查询/健康/菜单），输出简易 TPS 基线。

用法（本地）：
  python scripts/压测_核心链路.py --base http://127.0.0.1:8000 --concurrency 20 --requests 200
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from urllib.parse import urljoin

import httpx


async def _one(client: httpx.AsyncClient, path: str) -> float:
    t0 = time.perf_counter()
    r = await client.get(path)
    r.raise_for_status()
    return (time.perf_counter() - t0) * 1000


async def run(base: str, path: str, concurrency: int, total: int) -> dict:
    latencies: list[float] = []
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:

        async def worker():
            async with sem:
                latencies.append(await _one(client, path))

        t0 = time.perf_counter()
        await asyncio.gather(*[worker() for _ in range(total)])
        elapsed = time.perf_counter() - t0
    latencies.sort()
    n = len(latencies)
    return {
        "path": path,
        "requests": n,
        "concurrency": concurrency,
        "elapsed_s": round(elapsed, 3),
        "tps": round(n / elapsed, 2) if elapsed else 0,
        "p50_ms": round(latencies[n // 2], 2),
        "p95_ms": round(latencies[int(n * 0.95) - 1], 2),
        "p99_ms": round(latencies[max(0, int(n * 0.99) - 1)], 2),
        "avg_ms": round(statistics.mean(latencies), 2),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--path", default="/api/health")
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument("--requests", type=int, default=200)
    args = p.parse_args()
    report = asyncio.run(run(args.base.rstrip("/"), args.path, args.concurrency, args.requests))
    print(report)
    print(
        "容量提示：TPS≈"
        f"{report['tps']} 时，gunicorn workers ≈ ceil(目标峰值TPS / 单worker实测TPS)；"
        "DB max_connections ≥ workers×(pool_size+max_overflow)+预留。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

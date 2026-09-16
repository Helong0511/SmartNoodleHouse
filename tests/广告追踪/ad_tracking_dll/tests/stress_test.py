# -*- coding: utf-8 -*-
"""广告追踪引擎硬件感知并发压力测试。

默认自动根据 CPU 并发能力选择 workers/batch；也可显式 --workers/--batch。
不会一次性创建百万级 Future，避免压测脚本本身成为内存瓶颈。
"""
import argparse
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from tracking import TrackingEngine, TrackingError


def percentile(values, q):
    if not values:
        return 0.0
    data = sorted(values)
    pos = (len(data) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (pos - lo)


def main():
    ap = argparse.ArgumentParser(description="广告追踪引擎——跨平台硬件感知压力测试")
    ap.add_argument("--count", type=int, default=100000, help="总测试数量，默认 100000")
    ap.add_argument("--workers", type=int, default=0, help="并发线程；0=自动，根据硬件推荐")
    ap.add_argument("--batch", type=int, default=0, help="每批 Future 数量；0=自动，根据硬件推荐")
    ap.add_argument("--ttl", type=int, default=3600, help="Token TTL 秒，默认 3600")
    args = ap.parse_args()
    if args.count <= 0 or args.ttl <= 0 or args.workers < 0 or args.batch < 0:
        print("[错误] count、ttl 必须大于 0，workers/batch 必须 >= 0")
        raise SystemExit(2)

    e = TrackingEngine()
    e.set_key(1, "11" * 32)
    e.set_current_kid(1)
    hw = e.performance_info()
    workers = args.workers or int(hw.get("recommended_workers", max(1, min(os.cpu_count() or 1, 16))))
    batch = args.batch or int(hw.get("recommended_batch_size", 5000))
    workers = max(1, workers)
    batch = max(1, batch)

    print("=" * 76)
    print("广告追踪引擎 —— 硬件自适应生产压力测试")
    print("=" * 76)
    print(f"操作系统：{sys.platform} / {os.name}")
    print(f"Python：{sys.version.split()[0]}")
    print(f"CPU 逻辑核心：{hw.get('cpu_count', os.cpu_count() or 1)}")
    print(f"自动推荐线程：{hw.get('recommended_workers', '-')}")
    print(f"自动推荐批量：{hw.get('recommended_batch_size', '-')}")
    print(f"实际并发线程：{workers}")
    print(f"实际批量大小：{batch}")
    print(f"测试数量：{args.count}")
    print(f"Token TTL：{args.ttl} 秒")
    print()

    payload_base = {
        "offer_id": 10001, "campaign_id": 20001,
        "publisher_id": 30001, "advertiser_id": 40001,
        "source": "facebook", "utm_source": "facebook", "utm_medium": "cpc",
    }
    results = []
    errors = []
    latencies_ms = []

    def one(i):
        start = time.perf_counter_ns()
        payload = dict(payload_base)
        payload["sub_id"] = f"s{i}"
        token = e.generate(payload, args.ttl)
        parsed = e.verify(token)
        if parsed["offer_id"] != 10001:
            raise AssertionError("解密后的 offer_id 与原始数据不一致")
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        return token, parsed["click_id"], elapsed_ms

    start_all = time.perf_counter()
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for begin in range(0, args.count, batch):
            end = min(begin + batch, args.count)
            futures = [pool.submit(one, i) for i in range(begin, end)]
            for f in as_completed(futures):
                try:
                    results.append(f.result())
                    completed += 1
                    latencies_ms.append(results[-1][2])
                except Exception as exc:
                    errors.append(repr(exc))
    elapsed = time.perf_counter() - start_all

    tokens = [x[0] for x in results]
    click_ids = [x[1] for x in results]
    token_unique = len(tokens) == len(set(tokens))
    click_unique = len(click_ids) == len(set(click_ids))
    throughput = completed / elapsed if elapsed > 0 else 0.0

    sample = tokens[0] if tokens else None
    parse_ok = False
    tamper_ok = False
    structure_ok = False
    if sample:
        try:
            parsed = e.parse(sample)
            parse_ok = bool(parsed.get("click_id"))
            structure_ok = all(k in parsed for k in ("v", "kid", "iat", "exp", "click_id"))
        except Exception:
            pass
        try:
            bad = sample[:-1] + ("A" if sample[-1] != "A" else "B")
            e.verify(bad)
        except TrackingError:
            tamper_ok = True
        except Exception:
            tamper_ok = True

    invalid_ok = True
    for bad_token in ["", "abc", "!!!!", "AQ", "AQI=", "A" * 9000]:
        try:
            e.verify(bad_token)
            invalid_ok = False
            print(f"[失败] 非法 Token 被接受：{bad_token[:20]!r}")
        except Exception:
            pass

    print("[1/8] Token 生成与验证：", "通过" if completed == args.count and not errors else f"失败（{completed}/{args.count}）")
    print("[2/8] Token 唯一性：", "通过" if token_unique else "失败")
    print("[3/8] click_id 唯一性：", "通过" if click_unique else "失败")
    print("[4/8] 线程并发错误：", "通过" if not errors else "失败")
    print("[5/8] Token 解析：", "通过" if parse_ok else "失败")
    print("[6/8] Token 结构：", "通过" if structure_ok else "失败")
    print("[7/8] 篡改 Token 拒绝：", "通过" if tamper_ok else "失败")
    print("[8/8] 非法 Token 拒绝：", "通过" if invalid_ok else "失败")
    print()
    print("=" * 76)
    print("性能结果")
    print("=" * 76)
    print(f"总耗时：{elapsed:.3f} 秒")
    print(f"Generate + Verify 吞吐：{throughput:.2f} 次/秒")
    if latencies_ms:
        print(f"延迟 P50：{percentile(latencies_ms, 0.50):.3f} ms")
        print(f"延迟 P95：{percentile(latencies_ms, 0.95):.3f} ms")
        print(f"延迟 P99：{percentile(latencies_ms, 0.99):.3f} ms")
        print(f"平均延迟：{statistics.fmean(latencies_ms):.3f} ms")
        print(f"最大延迟：{max(latencies_ms):.3f} ms")
    print(f"错误数量：{len(errors)}")
    if errors:
        print("前 10 个错误：")
        for err in errors[:10]:
            print(f"  - {err}")

    overall = completed == args.count and not errors and token_unique and click_unique and parse_ok and structure_ok and tamper_ok and invalid_ok
    print("整体结论：PASS" if overall else "整体结论：FAIL")
    if not overall:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

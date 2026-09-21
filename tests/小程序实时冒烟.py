"""对运行中的后端做小程序链路冒烟（python tests/live_miniprogram_smoke.py）。"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

BASE = "http://127.0.0.1:8000"


def ok(name: str, cond: bool, detail: str = "") -> tuple[str, bool, str]:
    mark = "PASS" if cond else "FAIL"
    print(f"{mark} {name} {detail}".strip())
    return name, bool(cond), detail


async def _admin_token(c: httpx.AsyncClient) -> str:
    r = await c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    return (r.json().get("data") or {}).get("access_token", "")


async def _first_table(c: httpx.AsyncClient, admin_token: str) -> dict:
    r = await c.get(
        "/api/tables",
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"page_size": 1},
    )
    items = (r.json().get("data") or {}).get("items") or []
    return items[0] if items else {}


async def main() -> int:
    results: list[tuple[str, bool, str]] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as c:
        admin_token = await _admin_token(c)
        table = await _first_table(c, admin_token)
        table_token = table.get("qr_token") or ""
        store_id = table.get("store_id") or 1
        results.append(ok("获取桌台 token", bool(table_token), table.get("table_no") or ""))

        r = await c.post(
            "/api/miniprogram/wechat/login",
            json={"code": "live_flow_test", "nickname": "联调员", "table_token": table_token or None},
        )
        login = r.json().get("data") or {}
        token = login.get("customer_token", "")
        cid = login.get("customer_id")
        results.append(ok("微信登录", bool(token), f"customer_id={cid}"))
        if not token:
            return _summary(results)

        headers = {"Authorization": f"Bearer {token}"}

        r = await c.get("/api/miniprogram/customers/me", headers=headers)
        me = r.json().get("data") or {}
        results.append(
            ok("/customers/me", r.json().get("code") == 0 and me.get("customer_id"), me.get("nickname") or "")
        )

        r = await c.get("/api/miniprogram/customers/me/preferences", headers=headers)
        prefs = r.json().get("data") or {}
        all_p = prefs.get("all") or []
        rec = prefs.get("preferences") or []
        results.append(
            ok("/customers/me/preferences", r.json().get("code") == 0, f"all={len(all_p)} rec={len(rec)}")
        )

        if cid:
            r = await c.get(f"/api/customers/{cid}/preferences", headers=headers)
            old = r.json().get("data") or {}
            results.append(ok("旧偏好 API 条数一致", len(old.get("all") or []) == len(all_p)))

        r = await c.get("/api/products", params={"page_size": 20, "store_id": store_id})
        items = (r.json().get("data") or {}).get("items") or []
        results.append(ok("商品列表", bool(items), f"items={len(items)}"))
        if not items:
            return _summary(results)

        product = {}
        product_id = None
        groups: list = []
        for row in items:
            pid = row["id"]
            r = await c.get(f"/api/products/{pid}")
            detail = r.json().get("data") or {}
            g = detail.get("option_groups") or []
            if g:
                product = detail
                product_id = pid
                groups = g
                break
        if not product_id:
            product_id = items[0]["id"]
            r = await c.get(f"/api/products/{product_id}")
            product = r.json().get("data") or {}
            groups = product.get("option_groups") or []

        results.append(
            ok(
                "商品详情含 option_groups",
                bool(groups),
                f"product_id={product_id} groups={len(groups)}",
            )
        )
        if not table_token:
            return _summary(results)

        has_required = any(g.get("is_required") for g in groups)
        body = {
            "store_id": store_id,
            "table_token": table_token,
            "channel": "WECHAT",
            "items": [{"product_id": product_id, "quantity": 1, "option_ids": []}],
        }
        r = await c.post("/api/orders", json=body, headers=headers)
        msg = r.json().get("message") or ""
        if has_required:
            blocked = r.json().get("code") != 0 and ("规格" in msg or "必选" in msg)
            results.append(ok("缺规格下单拦截", blocked, msg[:80]))
        else:
            results.append(ok("缺规格下单拦截", True, "skip: 无必选规格商品"))

        opt_ids = []
        for group in groups:
            opts = group.get("options") or []
            if opts:
                normal = next((o for o in opts if o.get("code") == "NORMAL"), opts[0])
                opt_ids.append(normal["id"])
        if opt_ids:
            body = {
                "store_id": store_id,
                "table_token": table_token,
                "channel": "WECHAT",
                "idempotency_key": f"live_test_{int(time.time() * 1000)}",
                "items": [{"product_id": product_id, "quantity": 1, "option_ids": opt_ids}],
            }
            r = await c.post("/api/orders", json=body, headers=headers)
            order = r.json().get("data") or {}
            results.append(
                ok(
                    "带规格下单",
                    r.json().get("code") == 0 and bool(order.get("id")),
                    (r.json().get("message") or "")[:60],
                )
            )

    return _summary(results)


def _summary(results: list[tuple[str, bool, str]]) -> int:
    passed = sum(1 for _, cond, _ in results if cond)
    total = len(results)
    print(f"\n=== 联调 {passed}/{total} 通过 ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

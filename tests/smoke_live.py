"""Live smoke test against running backend (127.0.0.1:8000) and frontend (5173)."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
FE = "http://localhost:5173"
results: list[tuple[str, str, str]] = []


def req(method: str, path: str, data=None, token=None, base=BASE, timeout=20):
    url = base + path
    body = None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        body = json.dumps(data).encode("utf-8")
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                j = json.loads(raw)
            except Exception:
                j = {"_raw": raw[:300]}
            return resp.status, j
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(raw)
        except Exception:
            j = {"_raw": raw[:300]}
        return e.code, j
    except Exception as e:
        return 0, {"error": str(e)}


def check(name: str, ok: bool, detail: str = ""):
    status = "PASS" if ok else "FAIL"
    results.append((status, name, detail))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    code, data = req("GET", "/health")
    check("GET /health", code == 200 and data.get("status") == "ok", str(data))

    code, data = req("POST", "/api/v1/auth/login", {"username": "admin", "password": "admin123"})
    ok = code == 200 and data.get("code") == 0 and data.get("data")
    token = None
    user = {}
    if ok:
        token = data["data"].get("access_token")
        user = data["data"].get("user") or {}
    check(
        "POST /auth/login",
        bool(ok and token),
        f"biz={data.get('code')} role={user.get('role')} perms={len(user.get('permissions') or [])}",
    )
    if not token:
        print("ABORT: login failed")
        return 1

    code, data = req("GET", "/api/v1/auth/me", token=token)
    check("GET /auth/me", code == 200 and data.get("code") == 0, f"user={ (data.get('data') or {}).get('username') }")

    apis = [
        ("GET", "/api/v1/dashboard/overview"),
        ("GET", "/api/v1/products"),
        ("GET", "/api/v1/categories"),
        ("GET", "/api/v1/options/groups"),
        ("GET", "/api/v1/orders"),
        ("GET", "/api/v1/customers"),
        ("GET", "/api/v1/tables"),
        ("GET", "/api/v1/payments"),
        ("GET", "/api/v1/cameras"),
        ("GET", "/api/v1/cameras/overview"),
        ("GET", "/api/v1/delivery/orders"),
        ("GET", "/api/v1/delivery/stats"),
        ("GET", "/api/v1/welcome/logs"),
        ("GET", "/api/v1/settings"),
    ]
    for method, path in apis:
        code, data = req(method, path, token=token)
        biz = data.get("code") if isinstance(data, dict) else None
        msg = data.get("message") if isinstance(data, dict) else ""
        check(f"{method} {path}", code == 200 and biz == 0, f"http={code} biz={biz} msg={msg}")

    code, data = req("GET", "/api/v1/settings", token=token)
    app = (data.get("data") or {}).get("app") or {}
    pr = app.get("process_role")
    check("settings.process_role", pr is not None, f"process_role={pr}")

    try:
        with urllib.request.urlopen(FE + "/", timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            check("GET frontend /", resp.status == 200 and len(html) > 50, f"status={resp.status} len={len(html)}")
    except Exception as e:
        check("GET frontend /", False, str(e))

    code, data = req("GET", "/api/v1/auth/me", token=token, base=FE)
    check("Vite proxy /api/v1/auth/me", code == 200 and data.get("code") == 0, f"user={(data.get('data') or {}).get('username')}")

    fails = sum(1 for s, _, _ in results if s == "FAIL")
    print()
    print(f"SUMMARY: {len(results) - fails}/{len(results)} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

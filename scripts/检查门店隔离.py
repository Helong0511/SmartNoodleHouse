"""门店隔离覆盖率静态检查（P3-2）。

扫描 backend/app/api/*.py（统一 /api 前缀，无 api/v1 子目录），统计：
- DB_QUERY：数据库查询点（select( / db.get( / db.execute( / db.scalar(）
- GUARD   ：隔离守卫调用（require_bound_store / assert_store_access /
            assert_order_store_access / assert_customer_owns_order /
            assert_dining_session_owner / customer.id / .customer_id /
            store_scope / camera stream 等）

当某个 API 文件查询点较多但完全没有任何守卫调用时，以非零退出码告警
（该文件可能遗漏门店/顾客隔离）。

用法：
    python scripts/检查门店隔离.py            # CI，有疑似遗漏则失败
    python scripts/检查门店隔离.py --warn      # 仅告警不失败
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent / "backend" / "app" / "api"

QUERY_PATTERNS = [
    r"\bselect\s*\(",
    r"\.execute\s*\(",
    r"\.scalar\s*\(",
    r"\.get\s*\(\s*[A-Z]",  # db.get(Model, ...)
]
GUARD_PATTERNS = [
    r"require_bound_store",
    r"assert_store_access",
    r"assert_order_store_access",
    r"assert_customer_owns_order",
    r"assert_customer_can_pay_order",
    r"assert_dining_session_owner",
    r"store_scope",
    r"\.customer_id\b",
    r"customer\.id\b",
    r"CurrentCustomer",
    r"require_permission",  # 功能权限守卫（纯管理类接口至少有角色控制）
]

# 查询点达到该阈值且零守卫时判定疑似遗漏
MIN_QUERIES_TO_FLAG = 3

# 明确豁免：这些文件的查询不涉及门店/顾客数据隔离，或在请求前无法建立身份
# key=文件名，value=豁免理由（审计可追溯）
EXEMPT: dict[str, str] = {
    "auth.py": "登录/改密接口：认证建立前无身份上下文，按用户名查账号是预期行为",
    "health.py": "健康检查：无业务数据查询",
    "__init__.py": "路由聚合文件，无查询",
    "client_logs.py": "前端日志上报：只写不读，按来源 IP 限流",
    "deps.py": "依赖注入模块，非路由处理器",
    "orders_common.py": "订单路由共享辅助，无独立 HTTP 入口",
    "products_common.py": "菜品路由共享辅助，无独立 HTTP 入口",
}


def count_matches(text: str, patterns: list[str]) -> int:
    return sum(len(re.findall(p, text)) for p in patterns)


def main() -> int:
    warn_only = "--warn" in sys.argv
    if not API_DIR.is_dir():
        print(f"[isolation-check] API 目录不存在: {API_DIR}", file=sys.stderr)
        return 2

    files = sorted(API_DIR.glob("*.py"))
    total_queries = 0
    total_guards = 0
    flagged: list[tuple[str, int]] = []

    print("=" * 64)
    print(f"{'API 文件':<28}{'查询点':>8}{'守卫调用':>10}")
    print("-" * 64)
    for f in files:
        text = f.read_text(encoding="utf-8")
        q = count_matches(text, QUERY_PATTERNS)
        g = count_matches(text, GUARD_PATTERNS)
        total_queries += q
        total_guards += g
        print(f"{f.name:<28}{q:>8}{g:>10}")
        if q >= MIN_QUERIES_TO_FLAG and g == 0 and f.name not in EXEMPT:
            flagged.append((f.name, q))

    print("-" * 64)
    ratio = (total_guards / total_queries) if total_queries else 1.0
    print(f"{'合计':<28}{total_queries:>8}{total_guards:>10}  守卫/查询比={ratio:.2f}")
    print("=" * 64)

    if flagged:
        print("\n[警告] 以下文件存在多个数据库查询但未发现任何隔离/权限守卫：", file=sys.stderr)
        for name, q in flagged:
            print(f"  - {name}（查询点 {q}）", file=sys.stderr)
        print("\n请核对是否遗漏 require_bound_store / assert_store_access / "
              "customer.id 等隔离调用（参见 docs/STORE_ISOLATION_CHECKLIST.md）。",
              file=sys.stderr)
        return 0 if warn_only else 1

    print("\n[通过] 所有 API 文件均包含隔离/权限守卫。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

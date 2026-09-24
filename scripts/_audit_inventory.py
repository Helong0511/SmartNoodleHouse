"""一次性审计清单生成（可删）。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    api_dir = ROOT / "backend" / "app" / "api" / "v1"
    routes: list[tuple[str, str, str]] = []
    for f in sorted(api_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(
            r"@router\.(get|post|put|patch|delete)\([\"']([^\"']+)",
            text,
        ):
            routes.append((f.name, m.group(1).upper(), m.group(2)))

    print(f"API endpoints: {len(routes)}")
    for name, method, path in routes:
        print(f"  {method:6} {name:22} {path}")

    models_text = (ROOT / "backend" / "app" / "models" / "__init__.py").read_text(
        encoding="utf-8"
    )
    classes = re.findall(r"^class (\w+)\(", models_text, re.M)
    print(f"\nORM models ({len(classes)}):")
    print(", ".join(classes))

    services = sorted((ROOT / "backend" / "app" / "services").glob("*.py"))
    print(f"\nServices: {len(services)}")
    print(", ".join(p.stem for p in services if p.name != "__init__.py"))

    migrations = sorted((ROOT / "backend" / "alembic" / "versions").glob("*.py"))
    print(f"\nAlembic migrations: {len(migrations)}")
    print(", ".join(p.name for p in migrations))

    tests = sorted((ROOT / "tests").glob("测试_*.py"))
    print(f"\nBackend tests: {len(tests)}")

    fe_views = list((ROOT / "frontend" / "src" / "views").rglob("*.vue"))
    print(f"Frontend views: {len(fe_views)}")

    wx_pages = list((ROOT / "miniprogram-wechat").rglob("*.js"))
    ali_pages = list((ROOT / "miniprogram-alipay").rglob("*.js"))
    print(f"Wechat js files: {len(wx_pages)}; Alipay js files: {len(ali_pages)}")


if __name__ == "__main__":
    main()

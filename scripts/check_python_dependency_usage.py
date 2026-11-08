#!/usr/bin/env python3
"""核对后端直接依赖是否有源码引用或明确的框架/驱动用途。"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ_FILE = ROOT / "backend" / "requirements.txt"
SCAN_ROOTS = (ROOT / "backend" / "app", ROOT / "backend" / "alembic")

# PyPI 名称与 Python import 名称并不总是一致。
# 无静态 import 的依赖必须写明用途，避免“看起来没用”就误删数据库驱动或 CLI。
DEPENDENCY_MAP = {
    "fastapi": {"modules": {"fastapi"}},
    "starlette": {"modules": {"starlette"}},
    "uvicorn": {"reason": "ASGI 启动命令，不由业务模块 import"},
    "gunicorn": {"reason": "生产进程管理 CLI，不由业务模块 import"},
    "sqlalchemy": {"modules": {"sqlalchemy"}},
    "aiomysql": {"reason": "SQLAlchemy mysql+aiomysql 方言按数据库 URL 动态加载"},
    "alembic": {"modules": {"alembic"}, "reason": "迁移脚本与 Alembic CLI"},
    "pydantic": {"modules": {"pydantic"}},
    "pydantic-settings": {"modules": {"pydantic_settings"}},
    "python-dotenv": {"reason": "由 pydantic-settings 在读取 .env 时按需加载"},
    "pyjwt": {"modules": {"jwt"}},
    "passlib": {"modules": {"passlib"}},
    "bcrypt": {"reason": "由 Passlib 后端动态加载；版本被固定以维持兼容"},
    "redis": {"modules": {"redis"}},
    "httpx": {"modules": {"httpx"}},
    "python-multipart": {"reason": "FastAPI/Starlette 解析 multipart/form-data 时动态检测"},
    "apscheduler": {"modules": {"apscheduler"}},
    "websockets": {"reason": "由 Uvicorn standard extra 用于 WebSocket 协议处理"},
    "opencv-python-headless": {"modules": {"cv2"}},
    "numpy": {"modules": {"numpy"}},
    "cryptography": {"modules": {"cryptography"}},
    "pillow": {"modules": {"PIL"}},
    "pypinyin": {"modules": {"pypinyin"}},
    "lunar-python": {"modules": {"lunar_python"}},
    "insightface": {"modules": {"insightface"}},
    "onnxruntime": {"reason": "InsightFace 推理后端；显式锁定推理运行时，避免间接依赖漂移"},
    "pywin32": {"reason": "Windows SAPI 专用条件依赖，Linux 部署不安装"},
    "edge-tts": {"modules": {"edge_tts"}},
    "pymysql": {"reason": "Alembic 同步迁移连接使用 mysql+pymysql 方言动态加载"},
    "boto3": {"modules": {"boto3"}, "reason": "S3/MinIO 可选存储适配器"},
}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class ImportCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.modules: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        self.modules.update(alias.name.split(".", 1)[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.modules.add(node.module.split(".", 1)[0])


def main() -> int:
    modules: set[str] = set()
    scanned = 0
    for root in SCAN_ROOTS:
        for path in root.rglob("*.py"):
            if any(part in {".venv", "venv", "__pycache__"} for part in path.parts):
                continue
            scanned += 1
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError) as exc:
                print(f"源码无法解析：{path.relative_to(ROOT)}：{exc}", file=sys.stderr)
                return 1
            collector = ImportCollector()
            collector.visit(tree)
            modules.update(collector.modules)

    declared: set[str] = set()
    for raw in REQ_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)", line)
        if match:
            declared.add(canonical(match.group(1)))

    errors: list[str] = []
    for package in sorted(declared):
        entry = DEPENDENCY_MAP.get(package)
        if entry is None:
            errors.append(f"{package}: 尚未登记 import 映射或框架/驱动用途")
            continue
        expected = entry.get("modules", set())
        if expected and expected.isdisjoint(modules):
            reason = entry.get("reason")
            if not reason:
                errors.append(f"{package}: 未找到源码 import，且没有经审核的用途说明")
                continue
        if not expected and not entry.get("reason"):
            errors.append(f"{package}: 无源码 import，也没有用途说明")
    extra = set(DEPENDENCY_MAP) - declared
    if extra:
        errors.append("依赖映射中存在已从 requirements.txt 移除的项目：" + ", ".join(sorted(extra)))

    if errors:
        print("Python 依赖引用审计失败：", file=sys.stderr)
        print("\n".join(f" - {item}" for item in errors), file=sys.stderr)
        return 1
    print(f"Python 依赖引用审计通过：{len(declared)} 个直接依赖、{scanned} 个源码文件；未引用项均有明确用途说明。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

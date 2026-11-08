#!/usr/bin/env python3
"""审计后端依赖声明、实际导入与同作用域重复导入。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "backend" / "requirements.txt"
SOURCE_ROOTS = (ROOT / "backend" / "app", ROOT / "backend" / "alembic")

# Python 导入模块名与发行包名并不总是一致，在这里集中维护映射。
IMPORT_TO_DIST = {
    "fastapi": "fastapi",
    "starlette": "starlette",
    "sqlalchemy": "sqlalchemy",
    "aiomysql": "aiomysql",
    "alembic": "alembic",
    "pydantic": "pydantic",
    "pydantic_settings": "pydantic-settings",
    "dotenv": "python-dotenv",
    "jwt": "pyjwt",
    "jose": "python-jose",  # 防止未来又引入未声明的第二套 JWT 库
    "passlib": "passlib",
    "bcrypt": "bcrypt",
    "redis": "redis",
    "httpx": "httpx",
    "multipart": "python-multipart",
    "apscheduler": "apscheduler",
    "websockets": "websockets",
    "cv2": "opencv-python-headless",
    "numpy": "numpy",
    "cryptography": "cryptography",
    "PIL": "pillow",
    "pypinyin": "pypinyin",
    "lunar_python": "lunar-python",
    "insightface": "insightface",
    "onnxruntime": "onnxruntime",
    "win32api": "pywin32",
    "win32com": "pywin32",
    "pywintypes": "pywin32",
    "pythoncom": "pywin32",
    "winsound": "pywin32",
    "edge_tts": "edge-tts",
    "pymysql": "pymysql",
    "boto3": "boto3",
    "botocore": "boto3",  # boto3 自带的传递依赖，不单独重复声明
}

# 这些包通过命令行入口、框架插件或数据库 URL 动态加载，不会在业务代码里直接 import。
DYNAMIC_OR_ENTRYPOINT_DEPENDENCIES = {
    "uvicorn": "ASGI 服务启动入口",
    "gunicorn": "生产进程管理入口",
    "python-multipart": "FastAPI 表单/文件上传解析器按需加载",
    "aiomysql": "SQLAlchemy 根据 mysql+aiomysql URL 动态加载",
    "pymysql": "Alembic 同步迁移连接串动态加载",
    "python-dotenv": "Pydantic Settings 解析 .env 文件时加载",
    "bcrypt": "Passlib 按需加载 bcrypt 后端",
    "websockets": "Uvicorn standard extra 的 WebSocket 传输实现",
}


class SameScopeImportAudit(ast.NodeVisitor):
    """只检查同一作用域内的重复导入，避免误报不同函数中的延迟导入。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.scopes: list[set[tuple[str, str]]] = [set()]
        self.duplicates: list[str] = []

    def _record(self, module: str, imported: str, line: int) -> None:
        key = (module, imported)
        current = self.scopes[-1]
        if key in current:
            self.duplicates.append(f"{self.path.relative_to(ROOT)}:{line}: {module} -> {imported}")
        else:
            current.add(key)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record(alias.name, alias.asname or "", node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            return
        module = node.module or ""
        for alias in node.names:
            self._record(module, alias.name, node.lineno)

    def _visit_new_scope(self, node: ast.AST) -> None:
        # 装饰器、默认参数属于外层作用域；函数/类主体使用独立导入集合。
        for field in ("decorator_list", "args", "returns", "bases", "keywords"):
            value = getattr(node, field, None)
            if isinstance(value, list):
                for child in value:
                    if isinstance(child, ast.AST):
                        self.visit(child)
            elif isinstance(value, ast.AST):
                self.visit(value)
        self.scopes.append(set())
        for child in getattr(node, "body", []):
            self.visit(child)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_new_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_new_scope(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_new_scope(node)

    def _visit_branch(self, body: list[ast.stmt]) -> None:
        # 条件分支、异常回退和循环体是独立执行路径；不能把互斥分支的延迟导入误判为重复。
        self.scopes.append(set())
        for child in body:
            self.visit(child)
        self.scopes.pop()

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._visit_branch(node.body)
        if node.orelse:
            self._visit_branch(node.orelse)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_branch(node.body)
        for handler in node.handlers:
            self.visit(handler)
        if node.orelse:
            self._visit_branch(node.orelse)
        if node.finalbody:
            self._visit_branch(node.finalbody)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type:
            self.visit(node.type)
        self._visit_branch(node.body)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                self.visit(item.optional_vars)
        self._visit_branch(node.body)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.visit_With(node)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.target)
        self.visit(node.iter)
        self._visit_branch(node.body)
        if node.orelse:
            self._visit_branch(node.orelse)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit_For(node)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._visit_branch(node.body)
        if node.orelse:
            self._visit_branch(node.orelse)

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        for case in node.cases:
            if case.guard:
                self.visit(case.guard)
            self._visit_branch(case.body)


def parse_requirements() -> tuple[set[str], list[str]]:
    declared: set[str] = set()
    errors: list[str] = []
    for line_no, raw in enumerate(REQUIREMENTS.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        try:
            name = canonicalize_name(Requirement(line).name)
        except Exception as exc:
            errors.append(f"{REQUIREMENTS.relative_to(ROOT)}:{line_no}: 依赖声明格式错误：{exc}")
            continue
        if name in declared:
            errors.append(f"{REQUIREMENTS.relative_to(ROOT)}:{line_no}: 重复声明依赖 {name}")
        declared.add(name)
    return declared, errors


def main() -> int:
    declared, errors = parse_requirements()
    used: set[str] = set()
    duplicate_imports: list[str] = []
    unknown_imports: set[str] = set()
    stdlib = set(sys.stdlib_module_names)

    for source_root in SOURCE_ROOTS:
        for path in sorted(source_root.rglob("*.py")):
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
            except (OSError, UnicodeError, SyntaxError) as exc:
                errors.append(f"{path.relative_to(ROOT)}: 无法解析 Python 源码：{exc}")
                continue

            audit = SameScopeImportAudit(path)
            audit.visit(tree)
            duplicate_imports.extend(audit.duplicates)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots = [node.module.split(".", 1)[0]]
                else:
                    continue

                for module_root in roots:
                    if module_root in stdlib or module_root in {"app", "camera"}:
                        continue
                    distribution = IMPORT_TO_DIST.get(module_root)
                    if distribution is None:
                        unknown_imports.add(module_root)
                        continue
                    normalized = canonicalize_name(distribution)
                    used.add(normalized)
                    if normalized not in declared and normalized not in DYNAMIC_OR_ENTRYPOINT_DEPENDENCIES:
                        errors.append(
                            f"{path.relative_to(ROOT)}: 导入 {module_root}，"
                            f"但 backend/requirements.txt 未声明 {distribution}"
                        )

    # 允许动态加载依赖不在源码中出现 import；其用途必须在上面的中文清单中写明。
    allowed = {canonicalize_name(name) for name in DYNAMIC_OR_ENTRYPOINT_DEPENDENCIES}
    unused = sorted(declared - used - allowed)

    if duplicate_imports:
        errors.extend(f"同一作用域重复导入：{item}" for item in duplicate_imports)

    if errors:
        print("依赖引用审计失败：", file=sys.stderr)
        for error in errors:
            print(f" - {error}", file=sys.stderr)
        return 1

    print(f"依赖引用审计通过：检查 {len(SOURCE_ROOTS)} 个源码目录，实际引用 {len(used)} 个发行包。")
    if unused:
        print("需要人工复核的未匹配依赖（不自动删除）：")
        for name in unused:
            print(f" - {name}")
    if unknown_imports:
        print("未映射的导入根名称（可能是项目内模块或可选扩展，请人工核对）：")
        print(" - " + ", ".join(sorted(unknown_imports)))
    print("动态加载依赖说明：")
    for name, reason in sorted(DYNAMIC_OR_ENTRYPOINT_DEPENDENCIES.items()):
        if canonicalize_name(name) in declared:
            print(f" - {name}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

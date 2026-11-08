#!/usr/bin/env python3
"""检查仓库 Python 源码中的重复 import；不自动删除任何导入。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("backend/app", "backend/alembic", "scripts", "tests")
SKIP_PARTS = {".venv", "venv", "__pycache__", "node_modules", "target", "dist"}


class ImportDuplicateVisitor(ast.NodeVisitor):
    """按词法作用域统计完全相同的 import 语句，避免误报不同函数的局部导入。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.scope_stack: list[str] = ["<module>"]
        self.seen: dict[tuple[str, str], int] = {}
        self.duplicates: list[tuple[int, int, str]] = []

    def _scope(self, node: ast.AST, label: str) -> None:
        self.scope_stack.append(f"{label}@{getattr(node, 'lineno', 0)}")
        for child in node.body:  # type: ignore[attr-defined]
            self.visit(child)
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # 装饰器和默认参数在外层作用域执行；函数体属于独立作用域。
        for child in (*node.decorator_list, *node.args.defaults, *node.args.kw_defaults):
            if child is not None:
                self.visit(child)
        self._scope(node, node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for child in (*node.decorator_list, *node.bases, *node.keywords):
            self.visit(child)
        self._scope(node, node.name)

    def _record(self, node: ast.AST) -> None:
        signature = ast.dump(node, include_attributes=False)
        scope = "/".join(self.scope_stack)
        key = (scope, signature)
        previous = self.seen.get(key)
        if previous is not None:
            self.duplicates.append((previous, node.lineno, signature))
        else:
            self.seen[key] = node.lineno

    def visit_Import(self, node: ast.Import) -> None:
        self._record(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._record(node)


def main() -> int:
    failures: list[str] = []
    scanned = 0
    for root_name in SCAN_ROOTS:
        root = ROOT / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            scanned += 1
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError) as exc:
                failures.append(f"{path.relative_to(ROOT)}: 无法解析源码：{exc}")
                continue
            visitor = ImportDuplicateVisitor(path)
            visitor.visit(tree)
            for first, second, signature in visitor.duplicates:
                failures.append(
                    f"{path.relative_to(ROOT)}:{second}: 与第 {first} 行重复导入：{signature}"
                )

    if failures:
        print("重复导入检查失败：", file=sys.stderr)
        print("\n".join(f" - {item}" for item in failures), file=sys.stderr)
        return 1
    print(f"重复导入检查通过：已扫描 {scanned} 个 Python 文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""审计 Vue 前端的包声明、源码导入、重复导入和 npm 锁文件。"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
PACKAGE_JSON = FRONTEND / "package.json"
PACKAGE_LOCK = FRONTEND / "package-lock.json"
SOURCE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".vue", ".mjs", ".cjs"}
BUILTINS = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console",
    "constants", "crypto", "dgram", "diagnostics_channel", "dns", "domain",
    "events", "fs", "http", "http2", "https", "module", "net", "os", "path",
    "perf_hooks", "process", "punycode", "querystring", "readline", "stream",
    "string_decoder", "sys", "timers", "tls", "trace_events", "tty", "url",
    "util", "v8", "vm", "wasi", "worker_threads", "zlib",
}
# 仅由 CLI、类型包或构建工具间接使用的包，不要求业务源码直接 import。
CLI_OR_TYPE_ONLY = {
    "vite", "vitest", "vue-tsc", "typescript", "@types/qrcode",
}


def package_name(specifier: str) -> str:
    parts = specifier.split("/")
    return "/".join(parts[:2]) if specifier.startswith("@") else parts[0]


def is_external(specifier: str) -> bool:
    return not (
        specifier.startswith((".", "/", "@/ ", "@/"))
        or specifier.startswith("#")
        or specifier.startswith("~")
        or specifier.startswith("node:")
        or specifier in BUILTINS
    )


def main() -> int:
    manifest = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    lock = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))
    declared: dict[str, str] = {}
    errors: list[str] = []

    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, spec in manifest.get(section, {}).items():
            if name in declared:
                errors.append(f"package.json 中重复声明 {name}（跨依赖分组）")
            declared[name] = spec

    locked_root = lock.get("packages", {}).get("", {})
    locked_declared = {}
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        locked_declared.update(locked_root.get(section, {}))
    if declared != locked_declared:
        errors.append("package.json 与 package-lock.json 根依赖声明不一致，请运行 npm install --package-lock-only")

    imports: dict[str, list[str]] = defaultdict(list)
    duplicate_imports: list[str] = []
    import_pattern = re.compile(
        r"""\b(?:import|export)\s+(?:[^;"'\n]*?\s+from\s+)?["']([^"']+)["']"""
    )
    dynamic_pattern = re.compile(r"""\bimport\(\s*["']([^"']+)["']\s*\)""")
    require_pattern = re.compile(r"""\brequire\(\s*["']([^"']+)["']\s*\)""")

    for path in sorted(FRONTEND.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if any(part in {"node_modules", "dist", "coverage", ".git"} for part in path.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeError as exc:
            errors.append(f"{path.relative_to(ROOT)}: 源码不是 UTF-8：{exc}")
            continue

        specifiers = (
            import_pattern.findall(source)
            + dynamic_pattern.findall(source)
            + require_pattern.findall(source)
        )
        seen: set[str] = set()
        for specifier in specifiers:
            if not is_external(specifier):
                continue
            name = package_name(specifier)
            imports[name].append(str(path.relative_to(ROOT)))
            if name in seen:
                duplicate_imports.append(f"{path.relative_to(ROOT)}: {name}")
            seen.add(name)
            if name not in declared:
                errors.append(f"{path.relative_to(ROOT)}: 引用了未声明的 npm 依赖 {name}")

    # package-lock v3 中同一包可能有多份版本；只报告，不擅自删掉合法传递依赖。
    versions: dict[str, set[str]] = defaultdict(set)
    for install_path, metadata in lock.get("packages", {}).items():
        if not install_path.startswith("node_modules/"):
            continue
        name = metadata.get("name") or install_path[len("node_modules/"):]
        versions[name].add(str(metadata.get("version", "")))
    duplicated_versions = {
        name: sorted(value for value in values if value)
        for name, values in versions.items()
        if len({value for value in values if value}) > 1
    }

    unused = sorted(set(declared) - set(imports) - CLI_OR_TYPE_ONLY)
    if errors:
        print("前端依赖引用审计失败：", file=sys.stderr)
        for error in errors:
            print(f" - {error}", file=sys.stderr)
        return 1

    print(f"前端依赖引用审计通过：检查 {len(imports)} 个外部包引用。")
    print(f"npm 锁文件包数：{sum(1 for p in lock.get('packages', {}) if p.startswith('node_modules/'))}")
    if duplicated_versions:
        print("存在多版本传递依赖（需结合 npm ls 判断是否可收敛）：")
        for name, values in sorted(duplicated_versions.items()):
            print(f" - {name}: {', '.join(values)}")
    else:
        print("未发现同名 npm 包被锁定为多个版本。")
    if duplicate_imports:
        print("同一源码文件重复导入（建议合并 import）：")
        for item in sorted(set(duplicate_imports)):
            print(f" - {item}")
    if unused:
        print("未发现源码直接引用的声明包（可能由脚本/插件/类型间接使用，请人工复核）：")
        for name in unused:
            print(f" - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

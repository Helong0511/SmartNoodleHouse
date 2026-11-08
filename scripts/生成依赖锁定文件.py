#!/usr/bin/env python3
"""生成 backend/requirements.lock（锁定当前环境的直接与传递依赖）。

用法:
  cd backend
  pip install -r requirements.txt
  python ../scripts/生成依赖锁定文件.py

本脚本不会把当前平台未安装的条件依赖静默删除；若目标平台的旧 pin
不满足 requirements.txt，必须在该目标平台安装依赖后重新生成。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "backend" / "requirements.txt"
LOCK = ROOT / "backend" / "requirements.lock"


def _parse_lines(text: str, source: str) -> list[Requirement]:
    result: list[Requirement] = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        try:
            result.append(Requirement(line))
        except Exception as exc:
            raise ValueError(f"{source}:{line_no}: invalid requirement {line!r}: {exc}") from exc
    return result


def _exact_pin(req: Requirement) -> Version:
    pins = [spec.version for spec in req.specifier if spec.operator == "=="]
    if len(pins) != 1:
        raise ValueError(f"{req.name}: lock entry must contain exactly one == pin")
    return Version(pins[0])


def _frozen_versions(text: str) -> dict[str, Version]:
    versions: dict[str, Version] = {}
    for req in _parse_lines(text, "pip freeze"):
        name = canonicalize_name(req.name)
        version = _exact_pin(req)
        previous = versions.get(name)
        if previous is not None and previous != version:
            raise ValueError(f"pip freeze contains conflicting pins for {req.name}")
        versions[name] = version
    return versions


def build_lock_text(
    requirements_text: str,
    existing_lock_text: str,
    frozen_text: str,
    *,
    environment: dict[str, str] | None = None,
    python_version: str | None = None,
) -> str:
    """Build a complete environment pin set while preserving extras and platform markers."""
    env = dict(default_environment())
    if environment:
        env.update(environment)

    direct = _parse_lines(requirements_text, "requirements.txt")
    existing = _parse_lines(existing_lock_text, "requirements.lock")
    frozen = _frozen_versions(frozen_text)

    existing_by_name: dict[str, Requirement] = {}
    for entry in existing:
        name = canonicalize_name(entry.name)
        if name in existing_by_name:
            raise ValueError(f"requirements.lock contains duplicate package pin: {entry.name}")
        existing_by_name[name] = entry

    direct_names: set[str] = set()
    output: dict[str, str] = {}
    for req in direct:
        name = canonicalize_name(req.name)
        if name in direct_names:
            raise ValueError(f"requirements.txt contains duplicate package: {req.name}")
        direct_names.add(name)

        is_active = req.marker is None or req.marker.evaluate(env)
        if is_active:
            pin = frozen.get(name)
            if pin is None:
                raise ValueError(
                    f"{req.name}: not installed in this environment; install requirements.txt first"
                )
        else:
            old = existing_by_name.get(name)
            if old is None:
                raise ValueError(
                    f"{req.name}: inactive on this platform and has no existing lock pin; "
                    "generate on the target platform first"
                )
            pin = _exact_pin(old)

        if req.specifier and pin not in req.specifier:
            raise ValueError(
                f"{req.name}: resolved/retained pin {pin} violates {req.specifier}; "
                "install the required version on the target platform and regenerate"
            )

        extras = f"[{','.join(sorted(req.extras))}]" if req.extras else ""
        marker = f"; {req.marker}" if req.marker else ""
        output[name] = f"{req.name}{extras}=={pin}{marker}"

    # 将当前环境中实际解析安装的传递依赖一并固定，避免 pip 下次安装时漂移。
    # pip/setuptools/wheel 属于安装工具链，不作为应用运行时依赖锁定。
    bootstrap_tools = {"pip", "setuptools", "wheel", "packaging"}
    for name, pin in frozen.items():
        if name not in direct_names and name not in bootstrap_tools:
            output[name] = f"{name}=={pin}"

    # 当前平台未安装的条件依赖（例如 Linux 上的 pywin32）保留原有 marker 与 pin。
    for name, old in existing_by_name.items():
        if name not in direct_names and name not in frozen:
            output[name] = str(old)

    header = (
        "# Full environment-resolved pins for direct and transitive runtime dependencies.\n"
        "# Generated from pip freeze; this pins versions but does not include artifact hashes.\n"
        "# CI runs pip-audit and verifies every installed runtime distribution is represented.\n"
        "# PEP 508 extras and inactive platform markers are preserved.\n"
        f"# Python: {python_version or sys.version.split()[0]}\n"
        "# Install: pip install -r backend/requirements.lock\n"
        "# Keep aligned with backend/requirements.txt.\n"
    )
    body = "\n".join(output[name] for name in sorted(output))
    return header + body + "\n"


def main() -> int:
    if not REQ.is_file():
        print(f"missing requirements file: {REQ}", file=sys.stderr)
        return 1
    frozen = subprocess.check_output(
        [sys.executable, "-m", "pip", "freeze", "--local"],
        text=True,
    )
    try:
        generated = build_lock_text(
            REQ.read_text(encoding="utf-8"),
            LOCK.read_text(encoding="utf-8") if LOCK.exists() else "",
            frozen,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if LOCK.exists() and LOCK.read_text(encoding="utf-8") == generated:
        print(f"requirements.lock already current: {LOCK}")
        return 0
    LOCK.write_text(generated, encoding="utf-8")
    print(f"Wrote {LOCK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

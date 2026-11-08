#!/usr/bin/env python3
"""Verify direct requirements are pinned, marker-aligned, and installed from requirements.lock."""
from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, distributions, version
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "backend" / "requirements.txt"
LOCK = ROOT / "backend" / "requirements.lock"


def parse(path: Path) -> list[Requirement]:
    parsed: list[Requirement] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        try:
            parsed.append(Requirement(line))
        except Exception as exc:
            raise SystemExit(f"{path}:{line_no}: invalid requirement: {line!r}: {exc}")
    return parsed


def active(req: Requirement, env: dict[str, str]) -> bool:
    return req.marker is None or req.marker.evaluate(env)


def main() -> int:
    env = default_environment()
    direct = parse(REQUIREMENTS)
    locked = parse(LOCK)
    by_name: dict[str, list[Requirement]] = {}
    for req in locked:
        by_name.setdefault(canonicalize_name(req.name), []).append(req)

    errors: list[str] = []
    for req in direct:
        name = canonicalize_name(req.name)
        candidates = by_name.get(name, [])
        if not candidates:
            errors.append(f"{req.name}: declared in requirements.txt but absent from requirements.lock")
            continue
        if len(candidates) != 1:
            errors.append(f"{req.name}: lock must contain exactly one entry")
            continue

        candidate = candidates[0]
        if req.marker != candidate.marker:
            errors.append(
                f"{req.name}: lock platform marker {candidate.marker!s} "
                f"does not match requirements marker {req.marker!s}"
            )
            continue

        exact = [
            spec.version
            for spec in candidate.specifier
            if spec.operator == "=="
        ]
        if len(exact) != 1:
            errors.append(f"{req.name}: lock must contain exactly one == pin")
            continue

        missing_extras = set(req.extras) - set(candidate.extras)
        if missing_extras:
            errors.append(
                f"{req.name}: lock omits required extras: {', '.join(sorted(missing_extras))}"
            )
            continue

        pinned = Version(exact[0])
        if req.specifier and pinned not in req.specifier:
            errors.append(f"{req.name}: locked {pinned} violates {req.specifier}")
            continue

        # Conditional dependencies must remain represented in the universal lock on
        # every OS, but only the active platform is expected to have them installed.
        if not active(req, env):
            continue

        try:
            installed = Version(version(req.name))
        except PackageNotFoundError:
            errors.append(f"{req.name}: pinned dependency {pinned} is not installed")
            continue
        if installed != pinned:
            errors.append(f"{req.name}: installed {installed} differs from lock pin {pinned}")

    # 生产锁必须覆盖当前 Linux 环境中安装的全部运行时分发包，而不只是直接依赖。
    # checker 所需的 packaging 和 Python 安装工具链不属于应用运行时依赖。
    locked_names = set(by_name)
    bootstrap_tools = {"pip", "setuptools", "wheel", "packaging"}
    for dist in distributions():
        raw_name = dist.metadata.get("Name")
        if not raw_name:
            continue
        installed_name = canonicalize_name(raw_name)
        if installed_name in bootstrap_tools or installed_name in locked_names:
            continue
        errors.append(
            f"{raw_name}: installed runtime distribution is missing from requirements.lock"
        )

    if errors:
        print("requirements.lock consistency check FAILED:", file=sys.stderr)
        for error in errors:
            print(f" - {error}", file=sys.stderr)
        return 1
    print(f"requirements.lock OK: {len(direct)} direct dependencies pinned and marker-aligned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify every active direct requirement is pinned and installed by requirements.lock.

This prevents CI from silently installing an incomplete curated lock file.
"""
from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version
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
    direct = [r for r in parse(REQUIREMENTS) if active(r, env)]
    locked = [r for r in parse(LOCK) if active(r, env)]
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
        exact = [
            spec.version
            for candidate in candidates
            for spec in candidate.specifier
            if spec.operator == "=="
        ]
        if len(exact) != 1:
            errors.append(f"{req.name}: lock must contain exactly one == pin")
            continue
        missing_extras = set(req.extras) - set().union(*(set(candidate.extras) for candidate in candidates))
        if missing_extras:
            errors.append(
                f"{req.name}: lock omits required extras: {', '.join(sorted(missing_extras))}"
            )
            continue
        pinned = Version(exact[0])
        if req.specifier and pinned not in req.specifier:
            errors.append(f"{req.name}: locked {pinned} violates {req.specifier}")
            continue
        try:
            installed = Version(version(req.name))
        except PackageNotFoundError:
            errors.append(f"{req.name}: pinned dependency {pinned} is not installed")
            continue
        if installed != pinned:
            errors.append(f"{req.name}: installed {installed} differs from lock pin {pinned}")

    if errors:
        print("requirements.lock consistency check FAILED:", file=sys.stderr)
        for error in errors:
            print(f" - {error}", file=sys.stderr)
        return 1
    print(f"requirements.lock OK: {len(direct)} active direct dependencies pinned and installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""一次性扫描：flush/commit 后未 refresh 就序列化 server_default 时间戳的风险点。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "backend" / "app"

# 同步序列化函数名（含 created_at/updated_at/obtained_at 等）
SERIALIZER_HINTS = (
    "_payload",
    "payload",
    "serialize",
    "_ser",
    "to_dict",
    "template_payload",
    "identity_payload",
    "_hold_payload",
    "schedule_payload",
    "_queue_payload",
    "_ro_ser",
    "_serialize",
    "level_admin_payload",
)

TS_ATTR = re.compile(
    r"\b([a-z_][a-z0-9_]*)\.(created_at|updated_at|obtained_at|published_at|"
    r"attributed_at|received_at|archived_at|started_at|joined_at|computed_at|"
    r"auto_updated_at)\b"
)


def find_sync_serializers() -> list[tuple[str, int, str, list[str]]]:
    out: list[tuple[str, int, str, list[str]]] = []
    for p in ROOT.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r"(?m)^def\s+(\w+)\s*\(([^)]*)\)\s*(?:->[^:]*)?:\n((?:[ \t].*\n|\n)*)", text):
            name, args, body = m.group(1), m.group(2), m.group(3)
            if not any(h in name for h in SERIALIZER_HINTS) and "serialize_dt" not in body and "isoformat" not in body:
                continue
            attrs = sorted({a.group(2) for a in TS_ATTR.finditer(body)})
            if not attrs:
                continue
            if "select(" in body and "return {" not in body:
                continue
            lineno = text[: m.start()].count("\n") + 1
            out.append((str(p.relative_to(ROOT.parent.parent)), lineno, name, attrs))
    return out


def find_post_flush_reads() -> list[tuple[str, int, int, str, str]]:
    """flush/commit 后窗口内直接读时间戳（非赋值），且未 refresh 同对象。"""
    findings: list[tuple[str, int, int, str, str]] = []
    for p in ROOT.rglob("*.py"):
        lines = p.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if not re.search(r"await\s+(db|session)\.(flush|commit)\(\)", line):
                continue
            refreshed: set[str] = set()
            for j in range(i + 1, min(len(lines), i + 50)):
                wl = lines[j]
                if wl.startswith("async def ") or (wl.startswith("def ") and not wl.startswith("def _")):
                    # 可能误伤嵌套，保守：空行+下一顶层函数才停
                    if not wl.startswith(" ") and not wl.startswith("\t"):
                        break
                for m in re.finditer(r"await\s+(?:db|session)\.refresh\((\w+)\)", wl):
                    refreshed.add(m.group(1))
                for m in TS_ATTR.finditer(wl):
                    obj, attr = m.group(1), m.group(2)
                    if obj in refreshed or obj in {"self", "cls"}:
                        continue
                    if re.match(rf"\s*{re.escape(obj)}\.{attr}\s*=", wl):
                        continue
                    findings.append(
                        (
                            str(p.relative_to(ROOT.parent.parent)),
                            i + 1,
                            j + 1,
                            f"{obj}.{attr}",
                            wl.strip()[:160],
                        )
                    )
    # dedupe
    seen: set[tuple] = set()
    uniq: list[tuple[str, int, int, str, str]] = []
    for f in findings:
        key = (f[0], f[2], f[3])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    return uniq


def find_create_then_serializer() -> list[tuple[str, int, str]]:
    """粗检：db.add + flush/commit 后调用含 payload/serialize 且未见 refresh。"""
    hits: list[tuple[str, int, str]] = []
    for p in ROOT.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if "db.add(" not in line and ".add(" not in line:
                continue
            # look ahead for flush/commit then serializer without refresh
            window = lines[i : i + 60]
            joined = "\n".join(window)
            if not re.search(r"await\s+(db|session)\.(flush|commit)\(\)", joined):
                continue
            if re.search(r"await\s+(db|session)\.refresh\(", joined):
                continue
            # serializer call after flush?
            ser = re.search(
                r"(?:return\s+)?(?:success\()?([A-Za-z_][\w.]*?(?:payload|serialize|_ser|_payload)\w*)\s*\(",
                joined,
            )
            if not ser:
                # also inline created_at in success dict after add
                if not re.search(r"\.(created_at|updated_at|obtained_at)\b", joined):
                    continue
                hint = "inline timestamp after add"
            else:
                hint = f"serializer {ser.group(1)}"
            # must have timestamp in nearby serializer definition OR inline
            hits.append((str(p.relative_to(ROOT.parent.parent)), i + 1, hint))
    seen: set[tuple] = set()
    out: list[tuple[str, int, str]] = []
    for h in hits:
        if h in seen:
            continue
        seen.add(h)
        out.append(h)
    return out


def main() -> None:
    print("=== Sync serializers that read timestamps ===")
    sers = find_sync_serializers()
    for path, ln, name, attrs in sers:
        print(f"  {path}:{ln} {name} -> {attrs}")

    print("\n=== Direct timestamp READ after flush/commit (no refresh) ===")
    for path, a, b, attr, snip in find_post_flush_reads():
        print(f"  {path}:L{a}->{b} {attr}")
        print(f"    {snip}")

    print("\n=== db.add then flush/commit without refresh + serializer/timestamp ===")
    for path, ln, hint in find_create_then_serializer():
        print(f"  {path}:{ln} {hint}")


if __name__ == "__main__":
    main()

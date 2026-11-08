#!/usr/bin/env python3
"""Reject broken encodings and non-LF line endings in tracked text files.

The repository stores source/configuration/documentation as UTF-8 without BOM.
Binary assets are intentionally excluded by extension.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".acss", ".axml", ".bat", ".c", ".cfg", ".conf", ".css", ".csv",
    ".editorconfig", ".env", ".example", ".go", ".h", ".html", ".ini",
    ".java", ".js", ".json", ".jsx", ".lock", ".md", ".mdc", ".mjs",
    ".mts", ".py", ".rst", ".rs", ".scss", ".sh", ".sql", ".svg",
    ".toml", ".ts", ".tsx", ".txt", ".vue", ".wxml", ".wxss", ".xml",
    ".yaml", ".yml",
}
TEXT_NAMES = {
    ".dockerignore", ".gitattributes", ".gitignore", ".gitmodules",
    ".npmrc", ".nvmrc", ".python-version", "Dockerfile", "Makefile",
    "Procfile", "LICENSE",
}


def tracked_text_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    paths: list[Path] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(raw.decode("utf-8"))
        name = relative.name
        if relative.suffix.lower() in TEXT_SUFFIXES or name in TEXT_NAMES:
            paths.append(ROOT / relative)
        elif name.startswith(".env.") or name.startswith(".env_"):
            paths.append(ROOT / relative)
    return paths


def validate_bytes(path: Path, raw: bytes) -> list[str]:
    errors: list[str] = []
    relative = path.relative_to(ROOT).as_posix()
    if raw.startswith(b"\xef\xbb\xbf"):
        errors.append(f"{relative}: UTF-8 BOM is not allowed")
    if b"\x00" in raw:
        errors.append(f"{relative}: NUL byte found in a text file")
        return errors
    try:
        decoded = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        errors.append(
            f"{relative}: invalid UTF-8 at byte {exc.start} "
            f"(reason: {exc.reason})"
        )
        return errors
    if "\ufffd" in decoded:
        errors.append(f"{relative}: Unicode replacement character U+FFFD found")
    if b"\r" in raw:
        errors.append(f"{relative}: CR/CRLF line ending found; use LF")
    return errors


def main() -> int:
    errors: list[str] = []
    checked = 0
    for path in tracked_text_paths():
        if not path.is_file():
            continue
        checked += 1
        errors.extend(validate_bytes(path, path.read_bytes()))
    if errors:
        print("Text encoding check failed:")
        for error in errors:
            print(f" - {error}")
        print(f"Checked {checked} tracked text files; found {len(errors)} issue(s).")
        return 1
    print(
        f"Text encoding check passed: {checked} tracked text files "
        "are UTF-8 without BOM and use LF."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

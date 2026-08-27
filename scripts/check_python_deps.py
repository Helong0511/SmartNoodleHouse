#!/usr/bin/env python3
"""校验 Python 版本与关键依赖可导入。"""
from __future__ import annotations

import importlib
import sys

MIN = (3, 11)
REQUIRED = [
    "fastapi",
    "sqlalchemy",
    "pydantic",
    "redis",
    "httpx",
    "cv2",
    "numpy",
    "cryptography",
    "jose",
]


def main() -> int:
    if sys.version_info[:2] < MIN:
        print(f"ERROR: need Python >={MIN[0]}.{MIN[1]}, got {sys.version}")
        return 1
    print(f"Python OK: {sys.version.split()[0]}")
    failed = []
    for name in REQUIRED:
        try:
            importlib.import_module(name)
            print(f"  OK {name}")
        except Exception as e:
            failed.append((name, str(e)))
            print(f"  FAIL {name}: {e}")
    # insightface 可选但生产建议有
    try:
        importlib.import_module("insightface")
        print("  OK insightface")
    except Exception as e:
        print(f"  WARN insightface: {e}")
    if failed:
        print("Missing required packages. Run: pip install -r backend/requirements.txt")
        return 2
    print("All required imports OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

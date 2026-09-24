# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
tests = [
    ROOT / "tests" / "测试_P0审计跟进.py",
    ROOT / "tests" / "测试_P0资金JWT隔离.py",
    ROOT / "tests" / "测试_生产级审计P0.py",
]
sys.exit(pytest.main(["-q", "--tb=short", *[str(p) for p in tests if p.is_file()]]))

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "上线前运维检查清单.py"


def test_release_checklist_blocks_without_operator_acknowledgement():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "[BLOCKED]" in result.stdout
    for blocker in ("BLK-001", "BLK-002", "BLK-003", "BLK-004", "BLK-005", "BLK-006", "BLK-007", "BLK-008", "P1-R2"):
        assert blocker in result.stdout
    assert "BLK-007" in result.stdout


def test_release_checklist_requires_explicit_acknowledgement():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--acknowledge"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "[ACKNOWLEDGED]" in result.stdout
    assert "不是系统自动验证" in result.stdout

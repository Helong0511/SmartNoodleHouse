# -*- coding: utf-8 -*-
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from tracking import hardware_profile


def test_hardware_profile_is_safe_and_positive():
    p = hardware_profile()
    assert p["logical_cpus"] >= 1
    assert p["recommended_workers"] >= 1
    assert p["recommended_batch_size"] >= 1
    assert p["security_limits_fixed"] is True

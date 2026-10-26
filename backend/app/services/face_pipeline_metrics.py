"""识别链路最近一次阶段耗时（进程内；供摄像头 runtime / 运维面板读取）。"""
from __future__ import annotations

import threading
import time
from typing import Any

_LOCK = threading.Lock()
_LAST: dict[int, dict[str, Any]] = {}


def record_recognition_metrics(camera_id: int, **fields: Any) -> None:
    cid = int(camera_id)
    payload = {k: v for k, v in fields.items() if v is not None}
    payload["at"] = time.time()
    with _LOCK:
        prev = _LAST.get(cid) or {}
        prev.update(payload)
        _LAST[cid] = prev


def get_recognition_metrics(camera_id: int) -> dict[str, Any] | None:
    with _LOCK:
        raw = _LAST.get(int(camera_id))
        return dict(raw) if raw else None

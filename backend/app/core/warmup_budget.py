"""启动/热缓存定容：按本机可用内存自动给出加载上限。

生产 Ubuntu 与本地 Windows 均走同一套档位；业务勿再散落硬编码人数。
"""
from __future__ import annotations

import logging
import sys
from functools import lru_cache

logger = logging.getLogger(__name__)

# 可用内存档位 → 人脸热集人数上限（硬顶 5000）
_FACE_TIERS: tuple[tuple[int, int], ...] = (
    (1536 * 1024 * 1024, 200),  # < 1.5 GiB
    (3072 * 1024 * 1024, 800),  # < 3 GiB
    (6144 * 1024 * 1024, 2000),  # < 6 GiB
)
_FACE_CAP_HARD = 5000
_LOW_MEMORY_BYTES = 1536 * 1024 * 1024


def available_memory_bytes() -> int | None:
    """本机当前可用物理内存（字节）；探测失败返回 None。"""
    if sys.platform.startswith("linux"):
        try:
            vals: dict[str, int] = {}
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    key = line.split(":", 1)[0]
                    if key in ("MemAvailable", "MemFree", "Buffers", "Cached"):
                        vals[key] = int(line.split()[1]) * 1024
            if "MemAvailable" in vals:
                return vals["MemAvailable"]
            # 旧内核无 MemAvailable：MemFree+Buffers+Cached 近似
            if "MemFree" in vals:
                return vals["MemFree"] + vals.get("Buffers", 0) + vals.get("Cached", 0)
            return None
        except Exception:
            logger.debug("读取 /proc/meminfo 失败", exc_info=True)
            return None
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return None
            return int(stat.ullAvailPhys)
        except Exception:
            logger.debug("GlobalMemoryStatusEx 失败", exc_info=True)
            return None
    return None


def face_cache_max_profiles(
    *,
    override: int = 0,
    available_bytes: int | None = None,
) -> int:
    """人脸热集人数上限。

    override>0：固定上限（本地调试可写很大近似全量）。
    override<=0：按可用内存自动定容；探测失败时用中档 800。
    """
    if int(override or 0) > 0:
        return min(int(override), _FACE_CAP_HARD)
    avail = available_bytes if available_bytes is not None else available_memory_bytes()
    if avail is None:
        return 800
    for threshold, cap in _FACE_TIERS:
        if avail < threshold:
            return cap
    return _FACE_CAP_HARD


def is_low_memory(*, available_bytes: int | None = None) -> bool:
    """可用内存低于 1.5GiB：启动预热应缩量（跳过 codec 全量等）。"""
    avail = available_bytes if available_bytes is not None else available_memory_bytes()
    if avail is None:
        return False
    return avail < _LOW_MEMORY_BYTES


@lru_cache(maxsize=1)
def cached_face_cap_from_env() -> int:
    """进程内缓存一次自动定容结果（Settings.override 变更需清 get_settings 缓存）。"""
    from app.core.config import get_settings

    s = get_settings()
    return face_cache_max_profiles(override=int(getattr(s, "face_cache_max_profiles", 0) or 0))

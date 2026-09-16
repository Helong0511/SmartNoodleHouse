# -*- coding: utf-8 -*-
"""跨平台、硬件感知的广告追踪引擎 ctypes 封装。

安全边界：本模块不会根据硬件降低 Token/密码学安全限制；硬件信息只用于
建议宿主进程的并发线程数和批量大小。真正的 Replay/Conversion 幂等必须由
Redis/PostgreSQL 等业务层负责。
"""
from __future__ import annotations

import ctypes
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional


class TrackingError(RuntimeError):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[tracking:{code}] {message}")


def _library_candidates() -> list[Path]:
    here = Path(__file__).resolve().parent
    root = here.parent
    system = platform.system().lower()
    if system == "windows":
        names = [root / "bin" / "ad_tracking_engine.dll", root / "target" / "release" / "ad_tracking_engine.dll"]
    elif system == "darwin":
        names = [root / "lib" / "libad_tracking_engine.dylib", root / "target" / "release" / "libad_tracking_engine.dylib"]
    else:
        names = [root / "lib" / "libad_tracking_engine.so", root / "target" / "release" / "libad_tracking_engine.so"]
    return names


def _default_library() -> Path:
    candidates = _library_candidates()
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _physical_memory_bytes() -> int | None:
    """标准库跨平台读取物理内存；失败时返回 None，不影响核心功能。"""
    system = platform.system()
    try:
        if system == "Linux":
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        elif system == "Windows":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys)
        elif system == "Darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, timeout=1)
            return int(out.strip())
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return None
    return None


def hardware_profile() -> Dict[str, Any]:
    """返回宿主机硬件感知配置；安全限制不会随硬件降低。"""
    cpu = os.cpu_count() or 1
    try:
        if hasattr(os, "sched_getaffinity"):
            cpu = max(1, len(os.sched_getaffinity(0)))
    except OSError:
        pass
    memory = _physical_memory_bytes()
    # Crypto engine 本身是无状态计算库，线程由宿主控制；这里给出保守建议。
    workers = min(cpu, 16)
    if memory is not None:
        if memory < 4 * 1024**3:
            workers = min(workers, 2)
        elif memory < 8 * 1024**3:
            workers = min(workers, 4)
    workers = max(1, workers)
    batch = 2000 if cpu <= 2 else 5000 if cpu <= 4 else 10000 if cpu <= 8 else 20000
    return {
        "os": platform.system(), "os_release": platform.release(),
        "architecture": platform.machine(), "python": platform.python_version(),
        "logical_cpus": cpu, "physical_memory_bytes": memory,
        "recommended_workers": workers, "recommended_batch_size": batch,
        "security_limits_fixed": True,
    }


class TrackingEngine:
    def __init__(self, library: Optional[str | os.PathLike[str]] = None):
        path = Path(library) if library else _default_library()
        if not path.exists():
            raise FileNotFoundError(f"广告追踪引擎原生库不存在：{path}")
        self.lib = ctypes.CDLL(str(path))
        c_char_p = ctypes.c_char_p
        c_char_pp = ctypes.POINTER(ctypes.c_char_p)

        self.lib.tracking_set_key.argtypes = [ctypes.c_ubyte, c_char_p]
        self.lib.tracking_set_key.restype = ctypes.c_int32
        self.lib.tracking_remove_key.argtypes = [ctypes.c_ubyte]
        self.lib.tracking_remove_key.restype = ctypes.c_int32
        self.lib.tracking_set_current_kid.argtypes = [ctypes.c_ubyte]
        self.lib.tracking_set_current_kid.restype = ctypes.c_int32
        self.lib.tracking_generate.argtypes = [c_char_p, ctypes.c_uint64, c_char_pp]
        self.lib.tracking_generate.restype = ctypes.c_int32
        self.lib.tracking_verify.argtypes = [c_char_p, c_char_pp]
        self.lib.tracking_verify.restype = ctypes.c_int32
        self.lib.tracking_parse.argtypes = [c_char_p, c_char_pp]
        self.lib.tracking_parse.restype = ctypes.c_int32
        self.lib.tracking_free.argtypes = [c_char_p]
        self.lib.tracking_free.restype = None
        self.lib.tracking_last_error.argtypes = []
        self.lib.tracking_last_error.restype = c_char_p
        self.lib.tracking_version.argtypes = []
        self.lib.tracking_version.restype = c_char_p
        self.lib.tracking_abi_version.argtypes = []
        self.lib.tracking_abi_version.restype = ctypes.c_uint32
        self.lib.tracking_performance_info.argtypes = [c_char_pp]
        self.lib.tracking_performance_info.restype = ctypes.c_int32

    def _error(self, code: int) -> TrackingError:
        p = self.lib.tracking_last_error()
        msg = p.decode("utf-8", "replace") if p else "native error"
        return TrackingError(code, msg)

    def _check(self, code: int) -> None:
        if code != 0:
            raise self._error(code)

    def set_key(self, kid: int, key_hex: str) -> None:
        if not isinstance(kid, int) or not 1 <= kid <= 255:
            raise ValueError("KID 必须是 1~255 的整数")
        if not isinstance(key_hex, str):
            raise TypeError("key_hex 必须是字符串")
        self._check(self.lib.tracking_set_key(kid, key_hex.encode("ascii")))

    def remove_key(self, kid: int) -> None:
        self._check(self.lib.tracking_remove_key(kid))

    def set_current_kid(self, kid: int) -> None:
        self._check(self.lib.tracking_set_current_kid(kid))

    def generate(self, payload: Dict[str, Any], ttl_seconds: int) -> str:
        if not isinstance(payload, dict):
            raise TypeError("payload 必须是 dict")
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
            raise TypeError("ttl_seconds 必须是整数")
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        out = ctypes.c_char_p()
        self._check(self.lib.tracking_generate(raw, ttl_seconds, ctypes.byref(out)))
        try:
            if not out.value:
                raise TrackingError(100, "原生库返回空 Token")
            return out.value.decode("ascii")
        finally:
            self.lib.tracking_free(out)

    def _decode(self, fn, token: str) -> Dict[str, Any]:
        if not isinstance(token, str):
            raise TypeError("token 必须是 str")
        try:
            raw = token.encode("ascii")
        except UnicodeEncodeError as exc:
            raise TrackingError(6, "Token 必须是 ASCII/Base64URL 字符") from exc
        out = ctypes.c_char_p()
        self._check(fn(raw, ctypes.byref(out)))
        try:
            if not out.value:
                raise TrackingError(100, "原生库返回空 JSON")
            value = json.loads(out.value.decode("utf-8"))
            if not isinstance(value, dict):
                raise TrackingError(100, "原生库返回的不是 JSON 对象")
            return value
        finally:
            self.lib.tracking_free(out)

    def verify(self, token: str) -> Dict[str, Any]:
        return self._decode(self.lib.tracking_verify, token)

    def parse(self, token: str) -> Dict[str, Any]:
        return self._decode(self.lib.tracking_parse, token)

    def performance_info(self) -> Dict[str, Any]:
        out = ctypes.c_char_p()
        self._check(self.lib.tracking_performance_info(ctypes.byref(out)))
        try:
            native = json.loads(out.value.decode("utf-8")) if out.value else {}
        finally:
            self.lib.tracking_free(out)
        host = hardware_profile()
        # 宿主检测优先；原生库 CPU 推荐作为回退/交叉校验。
        native_workers = int(native.get("recommended_workers", 1))
        host["native_cpu_count"] = int(native.get("cpu_count", host["logical_cpus"]))
        host["native_recommended_workers"] = native_workers
        host["recommended_workers"] = min(int(host["recommended_workers"]), native_workers)
        return host

    @property
    def version(self) -> str:
        p = self.lib.tracking_version()
        return p.decode("utf-8", "replace") if p else ""

    @property
    def abi_version(self) -> int:
        return int(self.lib.tracking_abi_version())


_default: Optional[TrackingEngine] = None


def configure(library: Optional[str | os.PathLike[str]] = None) -> TrackingEngine:
    global _default
    _default = TrackingEngine(library)
    return _default


def _engine() -> TrackingEngine:
    if _default is None:
        return configure()
    return _default


def set_key(kid: int, key_hex: str) -> None:
    _engine().set_key(kid, key_hex)


def remove_key(kid: int) -> None:
    _engine().remove_key(kid)


def set_current_kid(kid: int) -> None:
    _engine().set_current_kid(kid)


def generate(payload: Dict[str, Any], ttl_seconds: int) -> str:
    return _engine().generate(payload, ttl_seconds)


def verify(token: str) -> Dict[str, Any]:
    return _engine().verify(token)


def parse(token: str) -> Dict[str, Any]:
    return _engine().parse(token)


def performance_info() -> Dict[str, Any]:
    return _engine().performance_info()

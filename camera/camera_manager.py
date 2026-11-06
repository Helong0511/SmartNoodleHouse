import asyncio
import logging
import random
import threading
import time
from typing import Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# RTSP 打开/读超时（毫秒）。.set() 返回 True 仅表示属性被接受，不保证 FFMPEG 一定生效。
_RTSP_OPEN_TIMEOUT_MS = 8000
_RTSP_READ_TIMEOUT_MS = 8000
# stop join 须覆盖 open 超时，否则重启时旧线程仍可能占着 VideoCapture
_STOP_JOIN_TIMEOUT_SEC = max(12.0, _RTSP_OPEN_TIMEOUT_MS / 1000.0 + 4.0)


class CameraNotLeaderError(RuntimeError):
    """非 Leader worker 尝试操作摄像头时抛出。"""


def is_rtsp_frame_usable(frame: np.ndarray | None) -> bool:
    """过滤 HEVC/RTSP 半解码花屏（大面积纯灰 + 一侧碎条），避免写入识别缓冲。

    注意：低纹理墙面/夜视也可能 std 偏低；阈值偏保守，宁可多放几帧坏图
    也不要把正常低对比画面一律判死（误过滤会表现为「有预览无识别」）。
    """
    if frame is None or getattr(frame, "size", 0) == 0:
        return False
    try:
        h, w = int(frame.shape[0]), int(frame.shape[1])
        if h < 64 or w < 64:
            return False
        # 抽样降采样，开销极小
        step = max(1, min(h, w) // 96)
        sample = frame[::step, ::step]
        if sample.ndim == 3:
            gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
        else:
            gray = sample
        mean = float(np.mean(gray))
        std = float(np.std(gray))
        # 典型坏帧：整幅近灰 std 极低；或左/右大半幅几乎常数、仅一条竖带有内容
        if std < 8.0:
            return False
        # 左右各 1/4：若一侧几乎无纹理而另一侧有人，多为解码撕裂
        mid = gray.shape[1] // 2
        left = gray[:, : max(1, mid // 2)]
        right = gray[:, -max(1, mid // 2) :]
        left_std = float(np.std(left))
        right_std = float(np.std(right))
        if min(left_std, right_std) < 4.0 and max(left_std, right_std) > 18.0:
            # 再看是否大片接近 mean（灰底）
            flat_ratio = float(np.mean(np.abs(gray.astype(np.float32) - mean) < 6.0))
            if flat_ratio > 0.55:
                return False
        # 全图过平（办公室正常画面 std 通常 >15）
        if std < 12.0 and 90.0 <= mean <= 140.0:
            flat_ratio = float(np.mean(np.abs(gray.astype(np.float32) - mean) < 8.0))
            if flat_ratio > 0.72:
                return False
        return True
    except Exception:
        return False


class FrameBuffer:
    """线程安全的最新帧缓存。

    墙上时钟用于展示；帧年龄用 monotonic，避免 NTP/手动改时导致误判过期。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._wall_ts: float = 0.0
        self._mono_ts: float = 0.0

    def update(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame.copy()
            self._wall_ts = time.time()
            self._mono_ts = time.monotonic()

    def get_frame(self) -> tuple[np.ndarray | None, float]:
        """返回 (帧副本, 墙上时间戳)；无帧时 ts=0。"""
        with self._lock:
            if self._frame is None:
                return None, 0.0
            return self._frame.copy(), self._wall_ts

    def age_sec(self) -> float | None:
        """相对单调时钟的帧龄（秒）；无帧返回 None。"""
        with self._lock:
            if self._frame is None or self._mono_ts <= 0:
                return None
            return max(0.0, time.monotonic() - self._mono_ts)


class CameraWorker:
    """单摄像头单采集线程"""

    def __init__(self, camera_id: int, source: str, fps: int = 25):
        self.camera_id = camera_id
        self.source = source
        self.fps = fps
        self.frame_buffer = FrameBuffer()
        self.status = "OFFLINE"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cap: cv2.VideoCapture | None = None
        self._reconnect_delay = 1
        self._max_reconnect_delay = 30
        self._last_backend: str = ""
        self._consecutive_bad_frames = 0
        self._last_good_frame_mono: float = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"camera-{self.camera_id}"
        )
        self._thread.start()

    def stop(self) -> bool:
        """停止采集线程；线程未退出时不跨线程 release VideoCapture，避免与 read/open 竞态。"""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=_STOP_JOIN_TIMEOUT_SEC)
            if self._thread.is_alive():
                logger.critical(
                    "Camera %s stop: thread still alive after join timeout=%.1fs "
                    "(open/read 可能仍阻塞；不得启动同一路新采集线程)",
                    self.camera_id,
                    _STOP_JOIN_TIMEOUT_SEC,
                )
                return False
        self._release_cap()
        self.status = "OFFLINE"
        return True

    def _release_cap(self) -> None:
        cap = self._cap
        self._cap = None
        if cap is not None:
            try:
                cap.release()
            except Exception:
                logger.debug("Camera %s release ignored", self.camera_id, exc_info=True)

    def _interruptible_sleep(self, seconds: float) -> bool:
        """可被 stop 打断的等待；返回 True 表示应退出循环。"""
        return self._stop.wait(timeout=max(0.0, float(seconds)))

    def _connect(self) -> bool:
        if self._stop.is_set():
            self.status = "OFFLINE"
            return False
        self._release_cap()
        t0 = time.perf_counter()
        try:
            connect_source = self.source
            if not self.source.isdigit():
                # 每次连接/重连前解析、校验全部 DNS 结果，并把 URL 固定为 IP 字面量；
                # 避免 FFmpeg 在校验后再次解析 hostname 造成 DNS rebinding TOCTOU。
                from app.services.camera_credentials import pin_camera_url_for_connect

                connect_source = pin_camera_url_for_connect(self.source)
            if self.source.isdigit():
                self._last_backend = "DSHOW_OR_DEFAULT"
                self._cap = cv2.VideoCapture(int(self.source))
                open_timeout_applied = None
                read_timeout_applied = None
            else:
                # 海康等设备 UDP 易丢包/无画面；强制 RTSP over TCP
                import os

                os.environ.setdefault(
                    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
                    "rtsp_transport;tcp|fflags;nobuffer|max_delay;500000",
                )
                self._last_backend = "FFMPEG"
                # OpenCV 4.5+/5：优先 open 前 set 超时；.set True≠后端一定执行超时
                cap = cv2.VideoCapture()
                open_timeout_applied = False
                read_timeout_applied = False
                try:
                    open_timeout_applied = bool(
                        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, _RTSP_OPEN_TIMEOUT_MS)
                    )
                    read_timeout_applied = bool(
                        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, _RTSP_READ_TIMEOUT_MS)
                    )
                except Exception:
                    logger.debug(
                        "Camera %s timeout props set-before-open unsupported",
                        self.camera_id,
                        exc_info=True,
                    )
                opened = bool(cap.open(connect_source, cv2.CAP_FFMPEG))
                if opened:
                    try:
                        # 部分构建仅在 open 后接受属性；再试一次并记录
                        if not open_timeout_applied:
                            open_timeout_applied = bool(
                                cap.set(
                                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, _RTSP_OPEN_TIMEOUT_MS
                                )
                            )
                        if not read_timeout_applied:
                            read_timeout_applied = bool(
                                cap.set(
                                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, _RTSP_READ_TIMEOUT_MS
                                )
                            )
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception:
                        pass
                self._cap = cap if opened else None
                if self._cap is None:
                    try:
                        cap.release()
                    except Exception:
                        pass

            open_ms = int((time.perf_counter() - t0) * 1000)
            # stop 在阻塞 open 期间被置位：立刻释放，避免与新 Worker 双开同路
            if self._stop.is_set():
                self._release_cap()
                self.status = "OFFLINE"
                return False
            if self._cap is not None and self._cap.isOpened():
                self.status = "ONLINE"
                self._reconnect_delay = 1
                logger.info(
                    "Camera %s ONLINE backend=%s open_ms=%s "
                    "open_timeout_ms=%s open_timeout_set=%s "
                    "read_timeout_ms=%s read_timeout_set=%s "
                    "host=%s note=set_true_does_not_prove_ffmpeg_honors_timeout",
                    self.camera_id,
                    self._last_backend,
                    open_ms,
                    _RTSP_OPEN_TIMEOUT_MS if self._last_backend == "FFMPEG" else "-",
                    open_timeout_applied if self._last_backend == "FFMPEG" else "-",
                    _RTSP_READ_TIMEOUT_MS if self._last_backend == "FFMPEG" else "-",
                    read_timeout_applied if self._last_backend == "FFMPEG" else "-",
                    self._source_host_hint(),
                )
                return True
            logger.warning(
                "Camera %s open failed (isOpened=false) backend=%s open_ms=%s host=%s",
                self.camera_id,
                self._last_backend,
                open_ms,
                self._source_host_hint(),
            )
        except Exception as e:
            logger.error("Camera %s connect failed: %s", self.camera_id, e)
        self._release_cap()
        self.status = "OFFLINE"
        return False

    def _source_host_hint(self) -> str:
        raw = self.source or ""
        if raw.isdigit():
            return f"usb:{raw}"
        try:
            from urllib.parse import urlparse

            p = urlparse(raw)
            host = p.hostname or "?"
            port = f":{p.port}" if p.port else ""
            return f"{p.scheme}://{host}{port}{p.path or ''}"
        except Exception:
            return "(unparseable)"

    def _run(self) -> None:
        interval = 1.0 / max(self.fps, 1)
        while not self._stop.is_set():
            if not self._cap or not self._cap.isOpened():
                self.status = "RECONNECTING"
                if not self._connect():
                    jitter = random.uniform(
                        0, min(0.5, float(self._reconnect_delay) * 0.25)
                    )
                    if self._interruptible_sleep(self._reconnect_delay + jitter):
                        break
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2, self._max_reconnect_delay
                    )
                    continue
            ret, frame = self._cap.read()
            if not ret:
                self.status = "OFFLINE"
                self._release_cap()
                jitter = random.uniform(0, min(0.5, float(self._reconnect_delay) * 0.25))
                if self._interruptible_sleep(self._reconnect_delay + jitter):
                    break
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )
                continue
            # 丢弃 HEVC 半帧/花屏，保留上一帧好画面供识别与抓拍
            self._apply_decode_result(frame, usable=is_rtsp_frame_usable(frame))
            if self._interruptible_sleep(interval):
                break

    def _apply_decode_result(self, frame, *, usable: bool) -> None:
        """好帧→ONLINE；坏帧且近期好帧≤5s→DEGRADED；否则 RECONNECTING（不计 usable）。"""
        if usable:
            self.frame_buffer.update(frame)
            self.status = "ONLINE"
            self._consecutive_bad_frames = 0
            self._last_good_frame_mono = time.monotonic()
            return
        self._consecutive_bad_frames += 1
        age = self.frame_buffer.age_sec()
        if age is not None and age <= 5.0:
            self.status = "DEGRADED"
        else:
            self.status = "RECONNECTING"


class CameraManager:
    """摄像头管理器 - 保证每摄像头仅一个 Worker；P0-6: Leader-only 守卫。"""

    _instance: "CameraManager | None" = None
    _workers: dict[int, CameraWorker]
    _lock: threading.RLock
    # P0-6: Leader 检查回调，由后端启动时注入；None 时不检查（兼容独立运行）
    _leader_check: Callable[[], bool] | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._workers = {}
            cls._instance._lock = threading.RLock()
            cls._instance._camera_operation_locks = {}
            cls._instance._stopping_all = False
        elif getattr(cls._instance, "_lock", None) is None:
            # 热重载旧单例无锁时补齐（RLock 是工厂函数，不能 isinstance(..., RLock)）
            cls._instance._lock = threading.RLock()
        if not hasattr(cls._instance, "_camera_operation_locks"):
            cls._instance._camera_operation_locks = {}
        if not hasattr(cls._instance, "_stopping_all"):
            cls._instance._stopping_all = False
        return cls._instance

    @classmethod
    def set_leader_check(cls, check: Callable[[], bool] | None) -> None:
        """注入 Leader 检查回调（后端启动时调用）。

        必须以 staticmethod 挂到类属性：裸函数作类属性时，经 instance 访问会
        被绑定成方法，从而把 CameraManager 当成第一参数传给 is_leader_held()。
        """
        cls._leader_check = staticmethod(check) if check is not None else None

    def _assert_leader(self) -> None:
        with self._lock:
            if self._stopping_all:
                raise CameraNotLeaderError("摄像头管理器正在停止；拒绝启动新采集线程")
        # 经类读取，避免 descriptor 绑定；兼容 staticmethod / 裸函数两种历史形态
        raw = type(self).__dict__.get("_leader_check", None)
        if isinstance(raw, staticmethod):
            check = raw.__func__
        else:
            check = raw
        if check is not None and not check():
            raise CameraNotLeaderError("摄像头操作需要 Leader 锁；非 Leader worker 拒绝执行")

    def _camera_operation_lock(self, camera_id: int) -> threading.Lock:
        with self._lock:
            return self._camera_operation_locks.setdefault(
                int(camera_id), threading.Lock()
            )

    def _cleanup_stale_workers(self) -> None:
        """P0-6: 清理线程已死的 stale worker，防止僵尸残留。调用方须已持 `_lock`。

        死线程 join 几乎立即返回；此处仅 signal+摘除，完整 join 由锁外路径兜底。
        """
        stale_ids = [
            cid
            for cid, w in self._workers.items()
            if w._thread and not w._thread.is_alive()
        ]
        for cid in stale_ids:
            logger.warning("Cleaning stale camera worker: %s", cid)
            w = self._workers.pop(cid, None)
            if w:
                w._stop.set()
                # 线程已确认死亡，可安全释放残留 VideoCapture，避免设备句柄泄漏。
                w._release_cap()
                w.status = "OFFLINE"

    def start_camera(
        self, camera_id: int, source: str, fps: int = 25, *, force: bool = False
    ) -> CameraWorker:
        op_lock = self._camera_operation_lock(camera_id)
        with op_lock:
            self._assert_leader()
            old: CameraWorker | None = None
            with self._lock:
                self._cleanup_stale_workers()
                existing = self._workers.get(camera_id)
                if existing and not force:
                    if existing._thread and existing._thread.is_alive():
                        return existing
                old = existing
                if old:
                    old._stop.set()

            # 不持全局锁 join；同一摄像头的 start/stop 由 per-camera lock 串行化。
            if old and not old.stop():
                raise RuntimeError(
                    f"camera {camera_id} old capture thread did not stop; refusing duplicate worker"
                )
            if old:
                with self._lock:
                    if self._workers.get(camera_id) is old:
                        self._workers.pop(camera_id, None)

            with self._lock:
                if self._stopping_all:
                    raise CameraNotLeaderError("摄像头管理器正在停止；拒绝启动新采集线程")
                self._assert_leader()
                existing = self._workers.get(camera_id)
                if existing and existing._thread and existing._thread.is_alive():
                    if not force:
                        return existing
                    raise RuntimeError(
                        f"camera {camera_id} already has a live worker; refusing duplicate worker"
                    )
                if existing:
                    self._workers.pop(camera_id, None)
                worker = CameraWorker(camera_id, source, fps)
                worker.start()
                self._workers[camera_id] = worker
                return worker

    def restart_camera(self, camera_id: int, source: str, fps: int = 25) -> CameraWorker:
        self._assert_leader()
        return self.start_camera(camera_id, source, fps, force=True)

    def list_camera_ids(self) -> list[int]:
        with self._lock:
            self._cleanup_stale_workers()
            return list(self._workers.keys())

    def get_worker(self, camera_id: int) -> CameraWorker | None:
        with self._lock:
            self._cleanup_stale_workers()
            return self._workers.get(camera_id)

    def _stop_camera_unlocked(self, camera_id: int) -> CameraWorker | None:
        """持锁调用：只从 map 摘除并 signal；调用方须在锁外 `worker.stop()` join。"""
        worker = self._workers.pop(camera_id, None)
        if worker:
            worker._stop.set()
        return worker

    def stop_camera(self, camera_id: int) -> None:
        # stop 允许非 Leader 调用；同一摄像头的 start/stop 串行化，但不持全局锁 join。
        with self._camera_operation_lock(camera_id):
            with self._lock:
                worker = self._workers.get(camera_id)
                if worker:
                    worker._stop.set()
            if worker and worker.stop():
                with self._lock:
                    if self._workers.get(camera_id) is worker:
                        self._workers.pop(camera_id, None)

    def stop_all_cameras(self, *, timeout: float = _STOP_JOIN_TIMEOUT_SEC) -> bool:
        """停止本进程全部采集线程；统一 deadline，防止多摄像头等待时间累加。

        停止期间拒绝所有新 start；同一摄像头操作锁串行化。未退出的 worker
        保留注册表且不释放 VideoCapture，返回 False 供 Leader 降级流程 fail-closed。
        """
        with self._lock:
            self._stopping_all = True
            workers = list(self._workers.items())
            operation_locks = [
                self._camera_operation_locks.setdefault(cid, threading.Lock())
                for cid, _worker in workers
            ]

        acquired: list[threading.Lock] = []
        try:
            for lock in operation_locks:
                lock.acquire()
                acquired.append(lock)

            with self._lock:
                workers = list(self._workers.items())
                for _camera_id, worker in workers:
                    worker._stop.set()

            deadline = time.monotonic() + max(0.1, float(timeout))
            stopped: list[tuple[int, CameraWorker]] = []
            alive_ids: list[int] = []
            for camera_id, worker in workers:
                thread = worker._thread
                if thread and thread.is_alive():
                    thread.join(timeout=max(0.0, deadline - time.monotonic()))
                if thread and thread.is_alive():
                    alive_ids.append(camera_id)
                    continue
                worker._release_cap()
                worker.status = "OFFLINE"
                stopped.append((camera_id, worker))

            with self._lock:
                for camera_id, worker in stopped:
                    if self._workers.get(camera_id) is worker:
                        self._workers.pop(camera_id, None)

            if alive_ids:
                logger.critical(
                    "停止摄像头未完全成功，仍运行 camera_ids=%s；禁止本进程重新竞选 Leader",
                    alive_ids,
                )
                return False
            logger.info("已停止全部摄像头采集线程 count=%s", len(stopped))
            return True
        finally:
            for lock in reversed(acquired):
                lock.release()

    def resume_camera_manager(self) -> None:
        """新 Leader 完成接管后重新允许摄像头启动。"""
        with self._lock:
            self._stopping_all = False

    def get_status(self, camera_id: int) -> str:
        with self._lock:
            self._cleanup_stale_workers()
            worker = self._workers.get(camera_id)
            return worker.status if worker else "OFFLINE"

    def get_runtime(self, camera_id: int) -> dict:
        with self._lock:
            self._cleanup_stale_workers()
            worker = self._workers.get(camera_id)
            if not worker:
                return {
                    "running": False,
                    "status": "OFFLINE",
                    "fps": 0,
                    "has_frame": False,
                }
            alive = bool(worker._thread and worker._thread.is_alive())
            if not alive:
                return {
                    "running": False,
                    "status": "OFFLINE",
                    "fps": 0,
                    "has_frame": False,
                }
            frame, ts = worker.frame_buffer.get_frame()
            age = worker.frame_buffer.age_sec()
            return {
                "running": True,
                "status": worker.status,
                "fps": worker.fps,
                "has_frame": frame is not None,
                "frame_age_sec": round(age, 2) if age is not None else None,
                "frame_wall_ts": ts if ts else None,
                "backend": getattr(worker, "_last_backend", "") or None,
                "consecutive_bad_frames": int(
                    getattr(worker, "_consecutive_bad_frames", 0) or 0
                ),
            }

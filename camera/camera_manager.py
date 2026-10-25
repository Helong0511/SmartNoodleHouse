import asyncio
import logging
import threading
import time
from typing import Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class CameraNotLeaderError(RuntimeError):
    """非 Leader worker 尝试操作摄像头时抛出。"""


def is_rtsp_frame_usable(frame: np.ndarray | None) -> bool:
    """过滤 HEVC/RTSP 半解码花屏（大面积纯灰 + 一侧碎条），避免写入识别缓冲。"""
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
    """线程安全的最新帧缓存"""

    def __init__(self):
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._timestamp: float = 0

    def update(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame.copy()
            self._timestamp = time.time()

    def get_frame(self) -> tuple[np.ndarray | None, float]:
        with self._lock:
            if self._frame is None:
                return None, 0
            return self._frame.copy(), self._timestamp


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

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"camera-{self.camera_id}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self._cap:
            self._cap.release()

    def _connect(self) -> bool:
        try:
            if self.source.isdigit():
                self._cap = cv2.VideoCapture(int(self.source))
            else:
                # 海康等设备 UDP 易丢包/无画面；强制 RTSP over TCP
                import os

                os.environ.setdefault(
                    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
                    "rtsp_transport;tcp|fflags;nobuffer|max_delay;500000",
                )
                self._cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
                try:
                    self._cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
                    self._cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000)
                    self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
            if self._cap is not None and self._cap.isOpened():
                self.status = "ONLINE"
                self._reconnect_delay = 1
                logger.info("Camera %s ONLINE", self.camera_id)
                return True
            logger.warning(
                "Camera %s open failed (isOpened=false) source_host=%s",
                self.camera_id,
                self._source_host_hint(),
            )
        except Exception as e:
            logger.error("Camera %s connect failed: %s", self.camera_id, e)
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
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
                    time.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
                    continue
            ret, frame = self._cap.read()
            if not ret:
                self.status = "OFFLINE"
                if self._cap:
                    self._cap.release()
                    self._cap = None
                time.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
                continue
            # 丢弃 HEVC 半帧/花屏，保留上一帧好画面供识别与抓拍
            if is_rtsp_frame_usable(frame):
                self.frame_buffer.update(frame)
            self.status = "ONLINE"
            time.sleep(interval)


class CameraManager:
    """摄像头管理器 - 保证每摄像头仅一个 Worker；P0-6: Leader-only 守卫。"""

    _instance: "CameraManager | None" = None
    _workers: dict[int, CameraWorker]
    # P0-6: Leader 检查回调，由后端启动时注入；None 时不检查（兼容独立运行）
    _leader_check: Callable[[], bool] | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._workers = {}
        return cls._instance

    @classmethod
    def set_leader_check(cls, check: Callable[[], bool] | None) -> None:
        """注入 Leader 检查回调（后端启动时调用）。

        必须以 staticmethod 挂到类属性：裸函数作类属性时，经 instance 访问会
        被绑定成方法，从而把 CameraManager 当成第一参数传给 is_leader_held()。
        """
        cls._leader_check = staticmethod(check) if check is not None else None

    def _assert_leader(self) -> None:
        # 经类读取，避免 descriptor 绑定；兼容 staticmethod / 裸函数两种历史形态
        raw = type(self).__dict__.get("_leader_check", None)
        if isinstance(raw, staticmethod):
            check = raw.__func__
        else:
            check = raw
        if check is not None and not check():
            raise CameraNotLeaderError("摄像头操作需要 Leader 锁；非 Leader worker 拒绝执行")

    def _cleanup_stale_workers(self) -> None:
        """P0-6: 清理线程已死的 stale worker，防止僵尸残留。"""
        stale_ids = [
            cid for cid, w in self._workers.items()
            if w._thread and not w._thread.is_alive()
        ]
        for cid in stale_ids:
            logger.warning("Cleaning stale camera worker: %s", cid)
            self.stop_camera(cid)

    def start_camera(self, camera_id: int, source: str, fps: int = 25, *, force: bool = False) -> CameraWorker:
        self._assert_leader()
        self._cleanup_stale_workers()
        existing = self._workers.get(camera_id)
        if existing and not force:
            # 已在跑则复用；卡死（线程死了）则重建
            if existing._thread and existing._thread.is_alive():
                return existing
            self.stop_camera(camera_id)
        elif existing and force:
            self.stop_camera(camera_id)
        worker = CameraWorker(camera_id, source, fps)
        worker.start()
        self._workers[camera_id] = worker
        return worker

    def restart_camera(self, camera_id: int, source: str, fps: int = 25) -> CameraWorker:
        self._assert_leader()
        return self.start_camera(camera_id, source, fps, force=True)

    def list_camera_ids(self) -> list[int]:
        return list(self._workers.keys())

    def get_worker(self, camera_id: int) -> CameraWorker | None:
        return self._workers.get(camera_id)

    def stop_camera(self, camera_id: int) -> None:
        # stop 允许非 Leader 调用（安全退出场景）
        worker = self._workers.pop(camera_id, None)
        if worker:
            worker.stop()

    def get_status(self, camera_id: int) -> str:
        worker = self._workers.get(camera_id)
        return worker.status if worker else "OFFLINE"

    def get_runtime(self, camera_id: int) -> dict:
        worker = self._workers.get(camera_id)
        if not worker:
            return {"running": False, "status": "OFFLINE", "fps": 0, "has_frame": False}
        frame, ts = worker.frame_buffer.get_frame()
        return {
            "running": bool(worker._thread and worker._thread.is_alive()),
            "status": worker.status,
            "fps": worker.fps,
            "has_frame": frame is not None,
            "frame_age_sec": round(time.time() - ts, 2) if ts else None,
        }

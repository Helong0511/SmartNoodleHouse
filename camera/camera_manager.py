import asyncio
import logging
import threading
import time
from typing import Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)


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
                self._cap = cv2.VideoCapture(self.source)
            if self._cap.isOpened():
                self.status = "ONLINE"
                self._reconnect_delay = 1
                return True
        except Exception as e:
            logger.error("Camera %s connect failed: %s", self.camera_id, e)
        self.status = "OFFLINE"
        return False

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
            self.frame_buffer.update(frame)
            self.status = "ONLINE"
            time.sleep(interval)


class CameraManager:
    """摄像头管理器 - 保证每摄像头仅一个 Worker"""

    _instance: "CameraManager | None" = None
    _workers: dict[int, CameraWorker]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._workers = {}
        return cls._instance

    def start_camera(self, camera_id: int, source: str, fps: int = 25, *, force: bool = False) -> CameraWorker:
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
        return self.start_camera(camera_id, source, fps, force=True)

    def list_camera_ids(self) -> list[int]:
        return list(self._workers.keys())

    def get_worker(self, camera_id: int) -> CameraWorker | None:
        return self._workers.get(camera_id)

    def stop_camera(self, camera_id: int) -> None:
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

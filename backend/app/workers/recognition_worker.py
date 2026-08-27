"""人脸识别 worker：从 CameraManager 取帧 → 匹配 → 迎宾（独立于 API 进程时可单独跑）"""
from __future__ import annotations

import asyncio
import logging
import time

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class RecognitionWorker:
    def __init__(self, poll_interval: float = 0.2):
        self.poll_interval = poll_interval
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        from app.core.database import AsyncSessionLocal
        from app.core.config import get_settings
        from app.services.face_extractor import extract_face_embedding
        from app.services.face_service import FaceService, WelcomeService
        from app.services.face_liveness import global_tracker
        from camera.camera_manager import CameraManager

        settings = get_settings()
        logger.info("RecognitionWorker started")
        while not self._stop.is_set():
            try:
                mgr = CameraManager()
                # 私有访问 workers；生产可改为公开 list_ids()
                camera_ids = list(getattr(mgr, "_workers", {}).keys())
                if not camera_ids:
                    await asyncio.sleep(1)
                    continue

                async with AsyncSessionLocal() as db:
                    profiles = await FaceService.get_authorized_profiles(db)
                    for cam_id in camera_ids:
                        worker = mgr.get_worker(cam_id)
                        if not worker or worker.status != "ONLINE":
                            continue
                        frame, ts = worker.frame_buffer.get_frame()
                        if frame is None or time.time() - ts > 2:
                            continue
                        ok, buf = cv2.imencode(".jpg", frame)
                        if not ok:
                            continue
                        try:
                            emb = np.array(extract_face_embedding(buf.tobytes()), dtype=np.float32)
                        except Exception:
                            continue
                        best_id, best_sim = None, 0.0
                        for cid, vec in profiles:
                            if vec.shape != emb.shape:
                                continue
                            sim = FaceService.cosine_similarity(emb, vec)
                            if sim > best_sim:
                                best_sim, best_id = sim, cid
                        if best_id is None or best_sim < settings.face_match_threshold * 0.9:
                            continue
                        confirmed, cid, avg = global_tracker.push(cam_id, emb, best_id, best_sim)
                        if confirmed and cid:
                            await WelcomeService.try_welcome(db, cid, cam_id, avg)
                            await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("RecognitionWorker 循环异常: %s", e)
            await asyncio.sleep(self.poll_interval)
        logger.info("RecognitionWorker stopped")

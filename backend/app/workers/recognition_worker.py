"""人脸识别 worker：从 CameraManager 取帧 → 匹配 → 迎宾（独立于 API 进程时可单独跑）"""
from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

logger = logging.getLogger(__name__)


class RecognitionWorker:
    def __init__(self, poll_interval: float = 0.2):
        self.poll_interval = poll_interval
        self._stop = asyncio.Event()
        self._last_detect_at: dict[int, float] = {}
        self._last_visitor_at: dict[int, float] = {}
        self._last_member_event_at: dict[str, float] = {}

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        from app.core.database import AsyncSessionLocal
        from app.core.config import get_settings
        from app.models import Camera
        from app.services.face_extractor import extract_face_embedding_bgr
        from app.services.face_service import FaceService, VisitorService, WelcomeService
        from app.services.face_liveness import global_tracker
        from camera.camera_manager import CameraManager
        from sqlalchemy import select

        settings = get_settings()
        logger.info(
            "RecognitionWorker started (unknown_visitor_capture=%s)",
            bool(getattr(settings, "unknown_visitor_capture_enabled", True)),
        )
        while not self._stop.is_set():
            try:
                mgr = CameraManager()
                camera_ids = mgr.list_camera_ids()
                if not camera_ids:
                    await asyncio.sleep(1)
                    continue

                async with AsyncSessionLocal() as db:
                    profiles = await FaceService.get_authorized_profiles(db)
                    cam_rows = {
                        c.id: c
                        for c in (
                            await db.execute(select(Camera).where(Camera.id.in_(camera_ids)))
                        ).scalars().all()
                    }
                    now = time.time()
                    for cam_id in camera_ids:
                        worker = mgr.get_worker(cam_id)
                        if not worker or worker.status != "ONLINE":
                            continue
                        cam = cam_rows.get(cam_id)
                        detect_fps = max(1, int(getattr(cam, "detect_fps", None) or 3))
                        min_interval = 1.0 / detect_fps
                        if now - self._last_detect_at.get(cam_id, 0) < min_interval:
                            continue
                        self._last_detect_at[cam_id] = now

                        # 实时帧缓冲，不落盘、不打开图片文件
                        frame, ts = worker.frame_buffer.get_frame()
                        if frame is None or now - ts > 2:
                            continue

                        try:
                            # 同步 InsightFace 绝不能堵事件循环，否则 TTS sleep/对讲会被拖到十几秒
                            emb_list = await asyncio.to_thread(extract_face_embedding_bgr, frame)
                            emb = np.array(emb_list, dtype=np.float32)
                        except Exception:
                            continue
                        best_id, best_sim = None, 0.0
                        for cid, vec in profiles:
                            if vec.shape != emb.shape:
                                continue
                            sim = FaceService.cosine_similarity(emb, vec)
                            if sim > best_sim:
                                best_sim, best_id = sim, cid
                        from app.services.system_settings_service import SystemSettingsService

                        store_id = int(cam.store_id) if cam and cam.store_id else None
                        threshold = await SystemSettingsService.effective_float(
                            db,
                            "face_match_threshold",
                            settings.face_match_threshold,
                            store_id=store_id,
                        )
                        # 未匹配到授权会员 → 未知访客抓拍 + 可选陌生客迎宾（按日目录存图，30s 节流）
                        if best_id is None or best_sim < threshold:
                            if best_id is not None:
                                logger.info(
                                    "人脸未达匹配阈值 camera_id=%s best_customer=%s sim=%.4f threshold=%.4f",
                                    cam_id,
                                    best_id,
                                    best_sim,
                                    threshold,
                                )
                            capture_on = await SystemSettingsService.effective_bool(
                                db,
                                "unknown_visitor_capture_enabled",
                                getattr(settings, "unknown_visitor_capture_enabled", True),
                                store_id=None,
                            )
                            # 抓拍 30s 节流；迎宾走 maybe_welcome 自身冷却，不因抓拍节流而静音
                            try:
                                if capture_on and now - self._last_visitor_at.get(cam_id, 0) >= 30:
                                    snap = await VisitorService.save_frame_snapshot(frame, cam_id)
                                    reuse_min = max(0.4, float(threshold) - 0.25)
                                    await VisitorService.record_event(
                                        db,
                                        cam_id,
                                        "ENTER",
                                        snapshot_path=snap,
                                        embedding=emb,
                                        reuse_min_sim=reuse_min,
                                    )
                                    self._last_visitor_at[cam_id] = now
                                await VisitorService.maybe_welcome_unknown(
                                    db, cam_id, store_id=store_id
                                )
                                await db.commit()
                            except Exception:
                                await db.rollback()
                                logger.exception("未知访客抓拍/迎宾失败 camera_id=%s", cam_id)
                            continue
                        confirmed, cid, avg = global_tracker.push(cam_id, emb, best_id, best_sim)
                        if confirmed and cid:
                            mem_key = f"{cam_id}:{cid}"
                            try:
                                if now - self._last_member_event_at.get(mem_key, 0) >= 30:
                                    snap = await VisitorService.save_frame_snapshot(frame, cam_id)
                                    await VisitorService.record_event(
                                        db,
                                        cam_id,
                                        "MATCH",
                                        snapshot_path=snap,
                                        embedding=emb,
                                        customer_id=int(cid),
                                    )
                                    self._last_member_event_at[mem_key] = now
                                await WelcomeService.try_welcome(db, cid, cam_id, avg)
                                await db.commit()
                            except Exception:
                                await db.rollback()
                                logger.exception(
                                    "会员识别落库/迎宾失败 camera_id=%s customer_id=%s",
                                    cam_id,
                                    cid,
                                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("RecognitionWorker 循环异常: %s", e)
            await asyncio.sleep(self.poll_interval)
        logger.info("RecognitionWorker stopped")

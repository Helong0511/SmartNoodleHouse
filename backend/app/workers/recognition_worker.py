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
        # 可观测：节流日志，避免无人脸时刷屏
        self._last_miss_log_at: dict[int, float] = {}
        self._last_stale_log_at: dict[int, float] = {}
        self._last_hit_log_at: dict[int, float] = {}
        self._miss_counts: dict[int, int] = {}
        self._hit_counts: dict[int, int] = {}
        # 迎宾入队后短暂让出 CPU，避免 InsightFace 拖慢 TTS/对讲首包
        self._tts_yield_until: dict[int, float] = {}
        # 近阈值暂缓陌生迎宾的起始时间（超时后仍按访客播，避免干等十余秒）
        self._near_defer_since: dict[int, float] = {}

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        from app.core.database import AsyncSessionLocal
        from app.core.config import get_settings
        from app.models import Camera
        from app.services.face_extractor import extract_face_embedding_bgr, ensure_live_face_app
        from app.services.face_service import (
            VisitorService,
            WelcomeService,
            face_embedding_cache,
        )
        from app.services.face_liveness import global_tracker
        from camera.camera_manager import CameraManager
        from sqlalchemy import select

        settings = get_settings()
        logger.info(
            "RecognitionWorker started (unknown_visitor_capture=%s)",
            bool(getattr(settings, "unknown_visitor_capture_enabled", True)),
        )
        # 等 live 模型就绪再检脸，避免启动期空跑漏检被当成「识别后很久才播」
        try:
            await asyncio.to_thread(ensure_live_face_app)
            logger.info("RecognitionWorker live 人脸模型就绪")
        except Exception:
            logger.exception("RecognitionWorker 等待 live 人脸模型失败，将边跑边加载")
        while not self._stop.is_set():
            try:
                mgr = CameraManager()
                camera_ids = mgr.list_camera_ids()
                if not camera_ids:
                    await asyncio.sleep(1)
                    continue

                async with AsyncSessionLocal() as db:
                    # 热集增量刷新一次/循环；单帧匹配走 best_match（含有界冷库）
                    await face_embedding_cache.get_profiles(db, copy=False)
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
                        if now < self._tts_yield_until.get(cam_id, 0):
                            continue
                        cam = cam_rows.get(cam_id)
                        detect_fps = max(1, int(getattr(cam, "detect_fps", None) or 3))
                        min_interval = 1.0 / detect_fps
                        if now - self._last_detect_at.get(cam_id, 0) < min_interval:
                            continue
                        self._last_detect_at[cam_id] = now

                        # 实时帧缓冲，不落盘、不打开图片文件
                        frame, ts = worker.frame_buffer.get_frame()
                        # 推理偶发变慢时放宽到 5s，避免「有预览却从不识别」
                        age = now - ts if ts else 999.0
                        if frame is None or age > 5.0:
                            if now - self._last_stale_log_at.get(cam_id, 0) >= 15.0:
                                self._last_stale_log_at[cam_id] = now
                                logger.warning(
                                    "识别跳过无帧/过期 camera_id=%s has_frame=%s age=%.2f status=%s",
                                    cam_id,
                                    frame is not None,
                                    age,
                                    getattr(worker, "status", None),
                                )
                            continue

                        try:
                            # 同步 InsightFace 绝不能堵事件循环；live=取最大脸+小 det_size
                            emb_list = await asyncio.to_thread(
                                extract_face_embedding_bgr, frame, live=True
                            )
                            emb = np.array(emb_list, dtype=np.float32)
                        except Exception as e:
                            self._miss_counts[cam_id] = self._miss_counts.get(cam_id, 0) + 1
                            misses = self._miss_counts.get(cam_id, 0)
                            if now - self._last_miss_log_at.get(cam_id, 0) >= 10.0:
                                self._last_miss_log_at[cam_id] = now
                                shape = getattr(frame, "shape", None)
                                logger.info(
                                    "识别未检出人脸 camera_id=%s misses=%s shape=%s err=%s",
                                    cam_id,
                                    misses,
                                    shape,
                                    str(e)[:100],
                                )
                            # 连续漏检时落一张诊断图，便于对照预览画面是否有人/对准磨砂玻璃。
                            # 勿写入 backend/uploads：uvicorn --reload 监视 cwd 时会因写 jpg 反复重启，
                            # 前端就会刷「后端暂时不可用（可能正在热重载）」。
                            if misses in (1, 30, 120) or (misses > 0 and misses % 200 == 0):
                                try:
                                    import tempfile
                                    from pathlib import Path

                                    import cv2
                                    import numpy as np

                                    dbg = Path(tempfile.gettempdir()) / "SmartNoodleHouse" / "face_debug"
                                    dbg.mkdir(parents=True, exist_ok=True)
                                    out = dbg / f"face_miss_cam{cam_id}.jpg"
                                    cv2.imwrite(str(out), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                                    mean = float(np.mean(frame)) if getattr(frame, "size", 0) else 0.0
                                    hint = ""
                                    # 中灰均值且长期无脸：常见是对准毛玻璃/墙面而非通道
                                    if 80.0 <= mean <= 160.0 and misses >= 30:
                                        hint = " 提示=画面均值中灰且长期无脸，请核对镜头是否对准通道而非磨砂玻璃/墙"
                                    logger.info(
                                        "已保存漏检诊断帧 path=%s misses=%s mean=%.0f%s",
                                        out.as_posix(),
                                        misses,
                                        mean,
                                        hint,
                                    )
                                except Exception:
                                    logger.debug("保存漏检诊断帧失败", exc_info=True)
                            continue

                        self._hit_counts[cam_id] = self._hit_counts.get(cam_id, 0) + 1
                        from app.services.system_settings_service import SystemSettingsService

                        store_id = int(cam.store_id) if cam and cam.store_id else None
                        threshold = await SystemSettingsService.effective_float(
                            db,
                            "face_match_threshold",
                            settings.face_match_threshold,
                            store_id=store_id,
                        )
                        best_id, best_sim = await face_embedding_cache.best_match(
                            db, emb, float(threshold), camera_id=cam_id
                        )
                        # 未匹配到授权会员 → 未知访客抓拍 + 可选陌生客迎宾
                        if best_id is None or best_sim < threshold:
                            if best_id is not None:
                                logger.info(
                                    "人脸未达匹配阈值 camera_id=%s best_customer=%s sim=%.4f threshold=%.4f",
                                    cam_id,
                                    best_id,
                                    best_sim,
                                    threshold,
                                )
                            elif now - self._last_hit_log_at.get(cam_id, 0) >= 5.0:
                                self._last_hit_log_at[cam_id] = now
                                logger.info(
                                    "识别到访客人脸 camera_id=%s hits=%s shape=%s → 陌生迎宾",
                                    cam_id,
                                    self._hit_counts.get(cam_id, 0),
                                    getattr(frame, "shape", None),
                                )
                            # 近阈值：短暂等待会员确认；超时仍播访客，避免干等 10–20s
                            near_margin = 0.08
                            near_max_wait = 1.5
                            defer_unknown = (
                                best_id is not None
                                and best_sim >= float(threshold) - near_margin
                            )
                            if defer_unknown:
                                since = self._near_defer_since.get(cam_id)
                                if since is None:
                                    self._near_defer_since[cam_id] = now
                                    since = now
                                waited = now - since
                                if waited < near_max_wait:
                                    logger.info(
                                        "近阈值暂缓陌生迎宾 camera_id=%s best_customer=%s "
                                        "sim=%.4f threshold=%.4f waited=%.1fs/%.1fs",
                                        cam_id,
                                        best_id,
                                        best_sim,
                                        threshold,
                                        waited,
                                        near_max_wait,
                                    )
                                else:
                                    logger.info(
                                        "近阈值超时改访客迎宾 camera_id=%s best_customer=%s "
                                        "sim=%.4f waited=%.1fs",
                                        cam_id,
                                        best_id,
                                        best_sim,
                                        waited,
                                    )
                                    defer_unknown = False
                            else:
                                self._near_defer_since.pop(cam_id, None)

                            if not defer_unknown:
                                # 先入队播报再抓拍，降低「认出到出声」延迟
                                try:
                                    t_hit = time.perf_counter()
                                    welcomed = await VisitorService.maybe_welcome_unknown(
                                        db, cam_id, store_id=store_id
                                    )
                                    await db.commit()
                                    if welcomed:
                                        self._near_defer_since.pop(cam_id, None)
                                        enqueue_ms = int((time.perf_counter() - t_hit) * 1000)
                                        logger.info(
                                            "访客迎宾已入队 camera_id=%s enqueue_ms=%s "
                                            "best_sim=%s",
                                            cam_id,
                                            enqueue_ms,
                                            f"{best_sim:.4f}" if best_id is not None else "-",
                                        )
                                        # 仅短暂让出，过长会导致人已离开才识别到会员
                                        self._tts_yield_until[cam_id] = time.time() + 0.6
                                    else:
                                        logger.debug(
                                            "陌生迎宾未入队（关闭或冷却） camera_id=%s",
                                            cam_id,
                                        )
                                except Exception:
                                    await db.rollback()
                                    logger.exception("未知访客迎宾失败 camera_id=%s", cam_id)

                            capture_on = await SystemSettingsService.effective_bool(
                                db,
                                "unknown_visitor_capture_enabled",
                                getattr(settings, "unknown_visitor_capture_enabled", True),
                                store_id=None,
                            )
                            if capture_on and now - self._last_visitor_at.get(cam_id, 0) >= 30:
                                try:
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
                                    await db.commit()
                                except Exception:
                                    await db.rollback()
                                    logger.exception(
                                        "未知访客抓拍落库失败 camera_id=%s",
                                        cam_id,
                                    )
                            continue

                        confirmed, cid, avg = global_tracker.push(cam_id, emb, best_id, best_sim)
                        if not confirmed or not cid:
                            logger.debug(
                                "会员多帧未确认 camera_id=%s customer_id=%s sim=%.4f",
                                cam_id,
                                best_id,
                                best_sim,
                            )
                            continue

                        logger.info(
                            "识别到会员 camera_id=%s customer_id=%s avg_sim=%.4f → 迎宾",
                            cam_id,
                            cid,
                            avg,
                        )
                        self._near_defer_since.pop(cam_id, None)
                        mem_key = f"{cam_id}:{cid}"
                        try:
                            # 先迎宾再落库，降低出声延迟
                            ok = await WelcomeService.try_welcome(
                                db, cid, cam_id, avg, preconfirmed=True
                            )
                            await db.commit()
                            if ok:
                                # 仅短暂让出给 TTS 首包；过长会拖下一位会员/复检
                                self._tts_yield_until[cam_id] = time.time() + 0.35
                            else:
                                logger.info(
                                    "会员迎宾未入队（冷却或阈值） camera_id=%s customer_id=%s",
                                    cam_id,
                                    cid,
                                )
                        except Exception:
                            await db.rollback()
                            logger.exception(
                                "会员迎宾失败 camera_id=%s customer_id=%s",
                                cam_id,
                                cid,
                            )
                        if now - self._last_member_event_at.get(mem_key, 0) >= 30:
                            try:
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
                                await db.commit()
                            except Exception:
                                await db.rollback()
                                logger.exception(
                                    "会员识别落库失败 camera_id=%s customer_id=%s",
                                    cam_id,
                                    cid,
                                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("RecognitionWorker 循环异常: %s", e)
            await asyncio.sleep(self.poll_interval)
        logger.info("RecognitionWorker stopped")

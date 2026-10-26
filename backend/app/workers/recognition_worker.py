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
        # 会员刚迎宾后抑制陌生播报，避免坏帧二次入队「欢迎光临」拖长体感
        self._suppress_unknown_until: dict[int, float] = {}
        # 每路摄像头短时择优窗口：走近过程中不用远景小人脸抢先定稿
        self._best_face_windows: dict[int, object] = {}

    def stop(self) -> None:
        self._stop.set()

    def _window_for(self, cam_id: int):
        from app.services.face_best_frame import BestFaceWindow

        win = self._best_face_windows.get(cam_id)
        if win is None:
            win = BestFaceWindow()
            self._best_face_windows[cam_id] = win
        return win

    async def run(self) -> None:
        from app.core.database import AsyncSessionLocal
        from app.core.config import get_settings
        from app.models import Camera
        from app.services.face_best_frame import BestFaceWindow
        from app.services.face_extractor import extract_live_face_hit, ensure_live_face_app
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
            "RecognitionWorker started (unknown_visitor_capture=%s best_face_window=2.8s)",
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
                        # DEGRADED：偶发坏帧但仍有近期好帧，须继续识别
                        if not worker or worker.status not in ("ONLINE", "DEGRADED"):
                            continue
                        if now < self._tts_yield_until.get(cam_id, 0):
                            continue
                        cam = cam_rows.get(cam_id)
                        detect_fps = max(1, int(getattr(cam, "detect_fps", None) or 3))
                        # 窗口已在收集：提高采样，更容易等到走近后的清晰脸
                        win = self._window_for(cam_id)
                        if getattr(win, "collecting", lambda: False)():
                            detect_fps = max(detect_fps, 6)
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
                        from camera.camera_manager import is_rtsp_frame_usable

                        if not is_rtsp_frame_usable(frame):
                            if now - self._last_stale_log_at.get(cam_id, 0) >= 12.0:
                                self._last_stale_log_at[cam_id] = now
                                logger.info(
                                    "识别跳过花屏/半解码帧 camera_id=%s shape=%s",
                                    cam_id,
                                    getattr(frame, "shape", None),
                                )
                            continue

                        decided = None
                        t_detect0 = time.perf_counter()
                        # 窗口收集中：轻量检测；尚未开窗或定稿前首检：完整路径
                        use_light = bool(getattr(win, "collecting", lambda: False)())
                        try:
                            # 同步 InsightFace 绝不能堵事件循环；带回质量分供窗口择优
                            hit = await asyncio.to_thread(
                                extract_live_face_hit, frame, light=use_light
                            )
                            detect_ms = int((time.perf_counter() - t_detect0) * 1000)
                        except Exception as e:
                            detect_ms = int((time.perf_counter() - t_detect0) * 1000)
                            try:
                                from app.services.face_pipeline_metrics import (
                                    record_recognition_metrics,
                                )

                                record_recognition_metrics(
                                    cam_id,
                                    detect_extract_ms=detect_ms,
                                    frame_age_ms=int(age * 1000),
                                    face_ok=False,
                                )
                            except Exception:
                                pass
                            # 漏检推进墙钟；超时仍有样本则强制定稿
                            try:
                                decided = win.prune_now(now)
                            except Exception:
                                decided = None
                            if decided is None:
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
                                # 勿写入 backend/uploads：uvicorn --reload 监视 cwd 时会因写 jpg 反复重启。
                                if misses in (1, 30, 120) or (misses > 0 and misses % 200 == 0):
                                    try:
                                        import tempfile
                                        from pathlib import Path

                                        import cv2

                                        dbg = (
                                            Path(tempfile.gettempdir())
                                            / "SmartNoodleHouse"
                                            / "face_debug"
                                        )
                                        dbg.mkdir(parents=True, exist_ok=True)
                                        out = dbg / f"face_miss_cam{cam_id}.jpg"
                                        cv2.imwrite(
                                            str(out), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80]
                                        )
                                        mean = (
                                            float(np.mean(frame))
                                            if getattr(frame, "size", 0)
                                            else 0.0
                                        )
                                        hint = ""
                                        if 80.0 <= mean <= 160.0 and misses >= 30:
                                            hint = (
                                                " 提示=画面均值中灰且长期无脸，请核对镜头是否"
                                                "对准通道而非磨砂玻璃/墙"
                                            )
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
                            logger.info(
                                "人脸窗口超时定稿(漏检触发) camera_id=%s",
                                cam_id,
                            )
                        else:
                            self._hit_counts[cam_id] = self._hit_counts.get(cam_id, 0) + 1
                            self._miss_counts[cam_id] = 0
                            try:
                                from app.services.face_pipeline_metrics import (
                                    record_recognition_metrics,
                                )

                                record_recognition_metrics(
                                    cam_id,
                                    detect_extract_ms=detect_ms,
                                    frame_age_ms=int(age * 1000),
                                    face_ok=True,
                                    face_area_ratio=round(float(hit.face_area_ratio), 4),
                                    det_score=round(float(hit.det_score), 3),
                                )
                            except Exception:
                                pass
                            decided = win.push(hit, frame=frame, now=now)
                            if decided is None:
                                if now - self._last_hit_log_at.get(cam_id, 0) >= 1.2:
                                    self._last_hit_log_at[cam_id] = now
                                    logger.info(
                                        "人脸窗口收集中 camera_id=%s ratio=%.4f sharp=%.1f "
                                        "det=%.3f score=%.3f（等更近/更清晰）",
                                        cam_id,
                                        hit.face_area_ratio,
                                        hit.sharpness,
                                        hit.det_score,
                                        BestFaceWindow.score(hit),
                                    )
                                continue

                        best_hit, best_frame, support_hits = decided
                        if best_frame is not None:
                            frame = best_frame
                            # 收集期可能用 light 特征；定稿对最佳帧再完整提一次再匹配
                            try:
                                t_refine0 = time.perf_counter()
                                refined = await asyncio.to_thread(
                                    extract_live_face_hit, best_frame, light=False
                                )
                                best_hit = refined
                                refine_ms = int(
                                    (time.perf_counter() - t_refine0) * 1000
                                )
                                try:
                                    from app.services.face_pipeline_metrics import (
                                        record_recognition_metrics,
                                    )

                                    record_recognition_metrics(
                                        cam_id,
                                        detect_extract_ms=refine_ms,
                                        face_ok=True,
                                        face_area_ratio=round(
                                            float(best_hit.face_area_ratio), 4
                                        ),
                                        det_score=round(float(best_hit.det_score), 3),
                                    )
                                except Exception:
                                    pass
                            except Exception:
                                logger.debug(
                                    "定稿完整提特征失败，沿用窗口 hit camera_id=%s",
                                    cam_id,
                                    exc_info=True,
                                )
                        emb = np.asarray(best_hit.embedding, dtype=np.float32)
                        logger.info(
                            "人脸窗口定稿 camera_id=%s ratio=%.4f sharp=%.1f det=%.3f "
                            "score=%.3f support=%s",
                            cam_id,
                            best_hit.face_area_ratio,
                            best_hit.sharpness,
                            best_hit.det_score,
                            BestFaceWindow.score(best_hit),
                            len(support_hits),
                        )
                        from app.services.system_settings_service import SystemSettingsService

                        store_id = int(cam.store_id) if cam and cam.store_id else None
                        threshold = await SystemSettingsService.effective_float(
                            db,
                            "face_match_threshold",
                            settings.face_match_threshold,
                            store_id=store_id,
                        )
                        t_match0 = time.perf_counter()
                        best_id, best_sim = await face_embedding_cache.best_match(
                            db, emb, float(threshold), camera_id=cam_id
                        )
                        match_ms = int((time.perf_counter() - t_match0) * 1000)
                        try:
                            from app.services.face_pipeline_metrics import (
                                record_recognition_metrics,
                            )

                            record_recognition_metrics(
                                cam_id,
                                match_ms=match_ms,
                                best_similarity=(
                                    round(float(best_sim), 4) if best_sim is not None else None
                                ),
                                matched_customer_id=best_id,
                                threshold=round(float(threshold), 4),
                            )
                        except Exception:
                            pass
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
                            # 远景小人脸：可试匹配会员，但禁止陌生迎宾/ENTER
                            # （17:33 走廊 ratio≈0.0002 sim≈0.17 误播「欢迎光临」）
                            face_ratio = float(getattr(best_hit, "face_area_ratio", 0.0) or 0.0)
                            min_visitor_ratio = 0.005
                            if face_ratio < min_visitor_ratio:
                                logger.info(
                                    "远景小人脸跳过陌生迎宾 camera_id=%s ratio=%.4f sim=%s "
                                    "（等走近再认/再迎）",
                                    cam_id,
                                    face_ratio,
                                    f"{best_sim:.4f}" if best_id is not None else "-",
                                )
                                continue

                            # 近阈值：多等一会儿让人走近再认；勿过早改访客
                            near_margin = 0.12
                            near_max_wait = 5.0
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

                            if (
                                not defer_unknown
                                and now < self._suppress_unknown_until.get(cam_id, 0)
                            ):
                                logger.info(
                                    "会员迎宾后抑制陌生播报 camera_id=%s remain=%.1fs",
                                    cam_id,
                                    self._suppress_unknown_until.get(cam_id, 0) - now,
                                )
                                defer_unknown = True

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
                                snap_frame = best_frame if best_frame is not None else frame
                                if is_rtsp_frame_usable(snap_frame):
                                    try:
                                        snap = await VisitorService.save_frame_snapshot(
                                            snap_frame, cam_id
                                        )
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
                                else:
                                    logger.info(
                                        "跳过花屏访客抓拍 camera_id=%s",
                                        cam_id,
                                    )
                            continue

                        # 同窗多帧一次喂给 tracker，避免「再等下一轮 2.6s 窗口」导致时有时无。
                        # 窗口本身已跨 2～4s 多样本；近重复向量也允许 push（时序确认，非再提特征）。
                        confirmed, cid, avg = False, None, float(best_sim)
                        support_sims: list[float] = []
                        for h in support_hits or [best_hit]:
                            e2 = np.asarray(h.embedding, dtype=np.float32)
                            mid, msim = await face_embedding_cache.best_match(
                                db, e2, float(threshold) - 0.05, camera_id=cam_id
                            )
                            if mid != best_id or msim < float(threshold) - 0.06:
                                continue
                            support_sims.append(float(msim))
                            confirmed, cid, avg = global_tracker.push(
                                cam_id, e2, best_id, float(msim)
                            )
                            if confirmed and cid:
                                break
                        # 窗口内已有 ≥2 帧同人达标 → 视作已确认（不必等下一窗）
                        if (not confirmed or not cid) and len(support_sims) >= 2:
                            confirmed, cid, avg = (
                                True,
                                best_id,
                                float(sum(support_sims) / len(support_sims)),
                            )
                            logger.info(
                                "会员同窗多样本确认 camera_id=%s customer_id=%s "
                                "n=%s avg_sim=%.4f",
                                cam_id,
                                cid,
                                len(support_sims),
                                avg,
                            )
                        # 高相似单次命中：仅高质量近景才允许跳过多帧确认，降低误配姓名风险
                        elif (
                            (not confirmed or not cid)
                            and best_sim >= float(threshold) + 0.04
                            and float(best_hit.face_area_ratio) >= 0.06
                            and float(best_hit.det_score) >= 0.55
                        ):
                            confirmed, cid, avg = True, best_id, float(best_sim)
                            logger.info(
                                "会员高相似跳过二次确认 camera_id=%s customer_id=%s "
                                "sim=%.4f ratio=%.4f det=%.3f",
                                cam_id,
                                cid,
                                avg,
                                best_hit.face_area_ratio,
                                best_hit.det_score,
                            )
                        if not confirmed or not cid:
                            logger.info(
                                "会员多帧未确认 camera_id=%s customer_id=%s sim=%.4f "
                                "support=%s（下一窗继续）",
                                cam_id,
                                best_id,
                                best_sim,
                                len(support_hits or []),
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
                                # 会员播报进行中勿再插「欢迎光临」（坏帧误判会再排 10s+）
                                self._suppress_unknown_until[cam_id] = time.time() + 25.0
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
                            snap_frame = best_frame if best_frame is not None else frame
                            if not is_rtsp_frame_usable(snap_frame):
                                logger.info(
                                    "跳过花屏会员抓拍 camera_id=%s customer_id=%s",
                                    cam_id,
                                    cid,
                                )
                            else:
                                try:
                                    snap = await VisitorService.save_frame_snapshot(
                                        snap_frame, cam_id
                                    )
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

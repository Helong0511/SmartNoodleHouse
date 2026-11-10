import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import app.bootstrap_path  # noqa: F401 — 项目根目录加入 sys.path（camera 模块）

from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import api_router
from app.api.health import root_router as health_root_router
from app.core.config import get_settings
from fastapi.exceptions import RequestValidationError

from app.core.exceptions import (
    AppException,
    app_exception_handler,
    generic_exception_handler,
    http_exception_handler,
    request_validation_exception_handler,
    sqlalchemy_integrity_handler,
    sqlalchemy_operational_handler,
)
from app.core.http_client import close_http_client
from app.core.intrusion_detection import detect_intrusion
from app.core.ip_allowlist import WebhookAllowlistMiddleware
from app.core.webhook_body_limit import WebhookBodyLimitMiddleware
from app.core.logging_config import setup_logging
from app.core.rate_limit import RateLimitMiddleware
from app.core.redis_client import RequestIDMiddleware
from app.core.startup_checks import validate_settings
from app.services.face_service import WelcomeService
from app.services.websocket_manager import on_redis_ws_message
from app.services.ws_pubsub import start_ws_subscriber, stop_ws_subscriber
from app.tasks.scheduler import register_tasks
from app.workers.tts_worker import TTSWorker

logger = logging.getLogger(__name__)


def _initialize_object_storage(settings) -> None:
    """启动时校验对象存储适配器，避免首次上传请求才暴露配置/依赖错误。"""
    try:
        from app.services.object_storage import get_storage

        get_storage()
    except Exception as exc:
        logger.exception(
            "对象存储初始化失败，拒绝启动（STORAGE_BACKEND=%s）",
            getattr(settings, "storage_backend", "local"),
        )
        raise SystemExit(1) from exc

scheduler = AsyncIOScheduler(
    timezone=ZoneInfo("Asia/Shanghai"),
    job_defaults={
        "coalesce": True,  # 积压多次触发合并为一次
        "max_instances": 1,  # 同进程内禁止同一任务重叠
        "misfire_grace_time": 60,
    }
)
tts_worker = TTSWorker()
tts_task: asyncio.Task | None = None
recognition_task: asyncio.Task | None = None
_leader_warmup_task: asyncio.Task | None = None
_leader_stop: asyncio.Event | None = None
_leader_renew_task: asyncio.Task | None = None
_leader_worker_id: str | None = None


async def _warmup_recognition_and_tts() -> None:
    """后台预热：先 InsightFace live（门口识别），再 TTS/G.711。

    旧顺序「先 TTS 再人脸」会让 RecognitionWorker 空跑 40s+ 漏检，体感像「认出访客要等十几秒」。
    """
    # 1) 仅在功能已开启且生产合规/模型商业授权均确认后预热人脸模型。
    # 不能因为启动预热绕过正式请求路径的授权门禁。
    try:
        from app.core.config import get_settings
        from app.core.database import AsyncSessionLocal
        from app.services.system_settings_service import SystemSettingsService

        settings = get_settings()
        from app.services.face_feature_gate import is_face_recognition_allowed

        face_approved = is_face_recognition_allowed(settings)
        face_enabled = False
        if face_approved:
            async with AsyncSessionLocal() as db:
                face_enabled = await SystemSettingsService.effective_bool(
                    db, "enable_face_welcome", settings.enable_face_welcome, store_id=None
                )
        if face_enabled:

            def _load_live() -> None:
                from app.services.face_extractor import _face_app_live

                _face_app_live()

            await asyncio.to_thread(_load_live)
            logger.info("InsightFace live 预热完成")
        else:
            logger.info("跳过 InsightFace 预热：功能未开启或生产合规/商业授权尚未确认")
    except Exception:
        logger.exception("InsightFace live 预热失败")

    try:
        from app.core.config import get_settings
        from app.core.database import AsyncSessionLocal
        from app.services.system_settings_service import SystemSettingsService
        from app.workers.tts_worker import warmup_tts_phrases

        settings = get_settings()
        # 摄像头对讲常用短句：WAV + G.711 一并预热，迎宾/试听命中缓存可跳过合成
        phrases = [
            "试听播报",
            "电脑试听",
            "欢迎光临",
            "欢迎回来",
            "欢迎回来。",
            "欢迎回来，尊贵的会员。",
            "欢迎回来，尊贵的会员",
            "摄像头扬声器播报测试",
            "电脑扬声器播报测试",
        ]
        try:
            async with AsyncSessionLocal() as db:
                unknown = await SystemSettingsService.effective_setting(
                    db,
                    "unknown_visitor_welcome_text",
                    fallback=getattr(settings, "unknown_visitor_welcome_text", None)
                    or "欢迎光临",
                )
                if unknown:
                    phrases.append(str(unknown).strip())
                greeting = await SystemSettingsService.effective_setting(
                    db,
                    "welcome_greeting_prefix",
                    fallback="欢迎回来，{称呼}。",
                )
                g = str(greeting or "").strip()
                if g and "{" not in g and "｛" not in g:
                    phrases.append(g)
                elif g:
                    # 模板含占位符：用常见取值预填几条整句，覆盖摄像头迎宾热路径
                    samples = (
                        {"称呼": "贵宾", "会员等级": "金卡会员", "店名": "本店", "名字": "贵宾"},
                        {"称呼": "会员", "会员等级": "会员", "店名": "射洪肠旺面", "名字": "会员"},
                        {"称呼": "尊贵的会员", "会员等级": "", "店名": "", "名字": "尊贵的会员"},
                    )
                    for sample in samples:
                        filled = g
                        for key, val in sample.items():
                            for ph in (f"{{{key}}}", f"｛{key}｝", f"{{ {key} }}"):
                                filled = filled.replace(ph, val)
                        # 压缩多余空白；仍含未替换占位则跳过
                        filled = " ".join(filled.split())
                        if "{" not in filled and "｛" not in filled and filled:
                            phrases.append(filled)
        except Exception:
            logger.debug("读取迎宾文案失败，仅预热内置短句", exc_info=True)

        volumes: tuple[int, ...] = (100, 80)
        try:
            async with AsyncSessionLocal() as db:
                vol = await SystemSettingsService.effective_int(
                    db,
                    "camera_speaker_volume_percent",
                    getattr(settings, "camera_speaker_volume_percent", 80),
                )
            volumes = tuple(sorted({100, 80, int(vol)}))
        except Exception:
            logger.debug("读取摄像头音量失败，使用默认 100/80", exc_info=True)

        stats = await asyncio.to_thread(
            warmup_tts_phrases, phrases, codecs=("alaw", "ulaw"), volumes=volumes
        )
        logger.info(
            "TTS 预热完成 phrases=%s wav_ok=%s g711=%s volumes=%s fail=%s",
            stats.get("phrases"),
            stats.get("wav_ok"),
            stats.get("g711"),
            stats.get("volumes"),
            stats.get("fail"),
        )
        # 预探测各摄像头 G.711 codec，避免首次对讲再等 ISAPI
        try:
            from sqlalchemy import select

            from app.core.warmup_budget import is_low_memory
            from app.models import Camera
            from app.services.camera_credentials import (
                build_connect_source,
                decrypt_password,
            )
            from app.services.hikvision_audio import HikvisionTwoWayAudio, resolve_isapi_base

            async with AsyncSessionLocal() as db:
                cams = (
                    await db.execute(select(Camera).where(Camera.status != "DISABLED"))
                ).scalars().all()
            # 低内存：最多探测 1 路，避免启动期挤占识别热集
            if is_low_memory() and cams:
                logger.warning("可用内存偏低，摄像头 codec 预热缩量至 1 路")
                cams = list(cams)[:1]
            warmed = 0
            for c in cams:
                src = (c.source_url or "").strip()
                if not src or src.isdigit():
                    continue
                base = resolve_isapi_base(src)
                if not base:
                    continue
                user = (c.username or "admin").strip() or "admin"
                password = ""
                if c.password_encrypted:
                    try:
                        password = decrypt_password(c.password_encrypted)
                    except Exception:
                        continue
                else:
                    try:
                        connected = build_connect_source(src, c.username, None)
                        from urllib.parse import unquote, urlparse

                        parsed = urlparse(connected)
                        if parsed.password:
                            password = unquote(parsed.password)
                        if parsed.username:
                            user = unquote(parsed.username)
                    except Exception:
                        continue
                if not password:
                    continue
                try:
                    # 启动时阻塞探测一次并写入 Redis，运行中播报走非阻塞 resolve
                    codec = await HikvisionTwoWayAudio(base, user, password).resolve_codec(
                        block=True
                    )
                    warmed += 1
                    logger.info(
                        "摄像头 codec 预热 camera_id=%s codec=%s",
                        c.id,
                        codec,
                    )
                except Exception:
                    logger.debug("摄像头 codec 预热失败 camera_id=%s", c.id, exc_info=True)
            logger.info("摄像头 codec 预热完成 count=%s", warmed)
        except Exception:
            logger.exception("摄像头 codec 预热失败")
    except Exception:
        logger.exception("TTS 预热失败")

    # 2) 入库用大图模型可后加载（不挡门口 live 识别）
    try:

        def _load_enroll() -> None:
            from app.services.face_extractor import _face_app

            _face_app()

        await asyncio.to_thread(_load_enroll)
        logger.info("InsightFace 入库模型预热完成")
    except Exception:
        logger.exception("InsightFace 入库模型预热失败")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tts_task, recognition_task, _leader_stop, _leader_renew_task, _leader_worker_id
    settings = get_settings()
    validate_settings(settings)

    _initialize_object_storage(settings)

    try:
        from sqlalchemy import text

        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        logger.info("数据库连接正常")
    except Exception as e:
        logger.error(
            "数据库连接失败：请在项目根目录 .env 配置 DATABASE_URL。"
            "示例：mysql+aiomysql://root:你的密码@127.0.0.1:3306/smart_noodle_house?charset=utf8mb4。"
            "错误：%s",
            e,
        )
        if settings.is_production:
            raise SystemExit(1) from e

    # 业务参数：把 META 中缺失键按 .env 种子写入 system_settings，并预热本进程缓存
    try:
        from app.core.database import AsyncSessionLocal
        from app.services.system_settings_service import SystemSettingsService

        async with AsyncSessionLocal() as db:
            await SystemSettingsService.ensure_seeded(db)
            from app.services.ab_service import AbService

            await AbService.ensure_default_experiment(db)
            await db.commit()
            await SystemSettingsService._load_raw(db, force=True)
        logger.info("system_settings 已种子并预热缓存")
    except Exception as e:
        logger.warning("system_settings 种子失败（可稍后由后台页触发）: %s", e)

    # 菜单缓存预热：启动时加载在营门店菜单到 Redis，避免首批请求缓存击穿
    try:
        from app.core.database import AsyncSessionLocal
        from app.core.warmup_budget import is_low_memory
        from app.services.menu_service import get_or_build_menu_bundle

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            from app.models import Store

            result = await db.execute(select(Store.id).where(Store.status == 1))
            store_ids = [r[0] for r in result.all()]
            if is_low_memory() and len(store_ids) > 3:
                logger.warning(
                    "可用内存偏低，菜单预热缩量 %s→3 家门店",
                    len(store_ids),
                )
                store_ids = store_ids[:3]
            for sid in store_ids:
                try:
                    await get_or_build_menu_bundle(db, sid)
                except Exception:
                    logger.debug("门店 %s 菜单预热失败", sid, exc_info=True)
        logger.info("菜单缓存预热完成 (%d stores)", len(store_ids))
    except Exception as e:
        logger.warning("菜单缓存预热失败（可稍后由首请求触发）: %s", e)

    # 必须走 settings（会读 .env）；勿用 os.getenv，否则仅写在 .env 的 PROCESS_ROLE 会被忽略
    from app.core.leader_lock import make_worker_id, release_leader, renew_leader_loop, try_acquire_leader

    role = (settings.process_role or "auto").strip().lower()
    _leader_worker_id = make_worker_id()
    if role == "worker":
        is_leader = False
    elif role in ("leader", "auto"):
        is_leader = await try_acquire_leader(_leader_worker_id)
        if role == "leader" and not is_leader:
            # 显式 leader 也必须尊重当前租约；禁止启动时无条件 DEL 其他 worker 的锁。
            # 旧 worker 崩溃时等待 TTL 到期，由 standby promote loop 自动接管。
            logger.warning(
                "PROCESS_ROLE=leader 当前已有其他 Leader 或 Redis 不可用；不强制删除锁，"
                "本进程进入 standby 并在后台重试（worker_id=%s）",
                _leader_worker_id,
            )
    else:
        logger.warning("未知 PROCESS_ROLE=%s，按 worker 处理", role)
        is_leader = False

    await start_ws_subscriber(on_redis_ws_message)
    _leader_promote_task: asyncio.Task | None = None

    async def _arm_standby_promote() -> None:
        """非持锁进程：持续争抢，Leader 宕机后自动接管（auto/leader）。"""
        nonlocal _leader_promote_task
        if role not in ("auto", "leader"):
            return
        if _leader_promote_task is not None and not _leader_promote_task.done():
            return
        _leader_promote_task = asyncio.create_task(_standby_promote_loop())

    async def _stop_leader_work() -> None:
        """锁丢失：停止 Leader 工作；只有确认旧任务退出后才允许本进程重新竞选。"""
        nonlocal is_leader
        global tts_task, recognition_task, _leader_warmup_task
        logger.warning("执行 Leader 降级：撤销数据库 epoch 并排空后台工作")
        from app.core.leader_fencing import deactivate_leader_epoch, revoke_database_epoch

        # 先禁止本进程启动新的 fenced job，再在数据库中推进 epoch。
        # 已运行事务持有的 ContextVar 仍会在 commit 时与 DB 当前 epoch 比较。
        deactivate_leader_epoch(_leader_worker_id)
        try:
            await asyncio.wait_for(
                revoke_database_epoch(_leader_worker_id),
                timeout=5.0,
            )
        except Exception:
            logger.exception(
                "Leader 丢锁后数据库 epoch 撤销失败；新 Leader 必须先成功递增 epoch 才能启动"
            )
        jobs_drained = True
        workers_drained = True
        try:
            from app.services.scheduler_admin import (
                active_leader_job_count,
                cancel_active_leader_jobs,
            )

            cancelled = await cancel_active_leader_jobs(timeout=5.0)
            remaining = active_leader_job_count()
            jobs_drained = remaining == 0
            logger.warning(
                "Leader 降级取消定时任务 count=%s remaining=%s",
                cancelled,
                remaining,
            )
            if not jobs_drained:
                logger.critical(
                    "仍有 %s 个 Leader 定时任务未退出；禁止本进程重新竞选，避免旧任务与新 Leader 并存",
                    remaining,
                )
        except Exception:
            jobs_drained = False
            logger.exception("取消运行中 Leader 定时任务失败；禁止本进程重新竞选")

        try:
            from app.services.scheduler_admin import stop_overrides_watcher

            await stop_overrides_watcher()
        except Exception:
            logger.exception("停止 overrides watcher 失败")
        try:
            if scheduler.running:
                scheduler.shutdown(wait=False)
        except Exception:
            logger.exception("关闭 scheduler 失败")
        try:
            tts_worker.stop()
        except Exception:
            logger.exception("停止 TTS worker 失败")

        async def _cancel_and_wait(task: asyncio.Task | None, label: str) -> bool:
            if task is None or task.done():
                return True
            task.cancel()
            done, _pending = await asyncio.wait({task}, timeout=5.0)
            if task not in done:
                logger.critical("%s 在 Leader 降级后 5 秒内未退出；禁止本进程重新竞选", label)
                return False
            try:
                task.result()
            except asyncio.CancelledError:
                return True
            except Exception:
                logger.exception("%s 在 Leader 降级时异常退出", label)
            return True

        tts_ok = await _cancel_and_wait(tts_task, "TTS worker")
        recognition_ok = await _cancel_and_wait(recognition_task, "RecognitionWorker")
        warmup_ok = await _cancel_and_wait(_leader_warmup_task, "Leader warmup")
        if tts_ok:
            tts_task = None
        if recognition_ok:
            recognition_task = None
        if warmup_ok:
            _leader_warmup_task = None

        camera_ok = False
        try:
            from camera.camera_manager import CameraManager

            camera_ok = CameraManager().stop_all_cameras()
        except Exception:
            logger.exception("Leader 降级停止摄像头采集失败")

        workers_drained = tts_ok and recognition_ok and warmup_ok and camera_ok
        is_leader = False
        # 若旧任务无法确认退出，本进程保持 standby，不允许 5 秒后重新成为 Leader。
        # 其他独立进程仍可在 Redis 租约到期后接管。
        if jobs_drained and workers_drained:
            await _arm_standby_promote()
        else:
            logger.critical(
                "Leader 降级未完成安全排空，本进程停止自动 promotion；需人工检查残留任务"
            )

    async def _run_leader_scoped(awaitable):
        """让长期 Leader worker 的所有 DB commit 同样经过 epoch fencing。"""
        from app.core.leader_fencing import leader_fence_scope

        with leader_fence_scope():
            return await awaitable

    async def _start_leader_work() -> None:
        """抢到锁后启动调度器 / TTS / 识别 / 摄像头。"""
        nonlocal is_leader, _leader_promote_task
        global tts_task, recognition_task, _leader_warmup_task, _leader_stop, _leader_renew_task
        from app.core.leader_lock import is_leader_held, set_leader_held, set_on_leader_lost

        # 成为 Leader 后停掉 standby 争抢。Promotion loop 可能正是当前 task，
        # 不能取消/await 自己，否则会触发 self-await RuntimeError。
        current_task = asyncio.current_task()
        if _leader_promote_task is current_task:
            _leader_promote_task = None
        elif _leader_promote_task is not None and not _leader_promote_task.done():
            _leader_promote_task.cancel()
            try:
                await _leader_promote_task
            except (asyncio.CancelledError, Exception):
                pass
            _leader_promote_task = None

        from app.core.leader_fencing import acquire_database_epoch, deactivate_leader_epoch

        deactivate_leader_epoch()
        try:
            epoch = await acquire_database_epoch(_leader_worker_id)
        except Exception:
            logger.exception(
                "Redis Leader 锁已获取，但数据库 fencing epoch 获取失败；拒绝启动任何 Leader 工作"
            )
            await release_leader(_leader_worker_id)
            is_leader = False
            await _arm_standby_promote()
            return

        # DB epoch 递增可能等待数据库锁；递增完成后必须再次确认 Redis 租约，
        # 否则旧竞选者可能在 Redis 锁已转移后才拿到一个更大的 DB epoch。
        from app.core.leader_lock import confirm_leader_owned

        if not await confirm_leader_owned():
            logger.error("数据库 epoch 获取期间 Redis Leader 租约已丢失，撤销本次 epoch")
            from app.core.leader_fencing import deactivate_leader_epoch, revoke_database_epoch

            deactivate_leader_epoch(_leader_worker_id)
            try:
                await asyncio.wait_for(
                    revoke_database_epoch(_leader_worker_id),
                    timeout=5.0,
                )
            except Exception:
                logger.exception("撤销未能确认 Redis 所有权的数据库 epoch 失败")
            await release_leader(_leader_worker_id)
            is_leader = False
            await _arm_standby_promote()
            return

        is_leader = True
        set_leader_held(True)
        set_on_leader_lost(_stop_leader_work)
        logger.warning(
            "Leader 数据库 fencing 已激活 epoch=%s worker_id=%s",
            epoch,
            _leader_worker_id,
        )
        # P0-6: 注入 Leader 检查回调到 CameraManager
        from camera.camera_manager import CameraManager

        CameraManager.set_leader_check(is_leader_held)
        CameraManager().resume_camera_manager()
        _leader_stop = asyncio.Event()
        _leader_renew_task = asyncio.create_task(renew_leader_loop(_leader_worker_id, _leader_stop))
        # reload 场景下可能曾启动过又关掉，避免重复 register
        if scheduler.running:
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                logger.exception("重启前关闭旧 scheduler 失败")
        # 清空旧 job（shutdown 后 job store 仍可能残留于同一实例）
        try:
            scheduler.remove_all_jobs()
        except Exception:
            pass
        register_tasks(scheduler)
        scheduler.start()
        try:
            from app.tasks.scheduler import apply_scheduler_overrides

            await apply_scheduler_overrides(scheduler)
        except Exception:
            logger.exception("应用定时任务覆盖失败")
        try:
            from app.services.scheduler_admin import start_overrides_watcher

            await start_overrides_watcher(scheduler)
        except Exception:
            logger.exception("启动定时任务覆盖订阅失败")
        logger.info("Scheduler started (leader worker_id=%s role=%s)", _leader_worker_id, role)
        # edge / local 均靠 TTSWorker 消费 Redis 迎宾队列；仅 local 时启会导致生产 edge 永不出声
        tts_task = asyncio.create_task(_run_leader_scoped(tts_worker.run()))
        logger.info(
            "TTS worker started (leader) provider=%s",
            getattr(settings, "tts_provider", "?"),
        )
        if settings.enable_recognition_worker:
            try:
                from app.workers.recognition_worker import RecognitionWorker

                recognition_task = asyncio.create_task(
                    _run_leader_scoped(RecognitionWorker().run())
                )
                logger.info("RecognitionWorker started (leader)")
            except Exception as e:
                logger.warning("RecognitionWorker 未启动: %s", e)
        try:
            from sqlalchemy import select

            from app.core.config import get_settings
            from app.core.database import AsyncSessionLocal
            from app.models import Camera
            from app.services.camera_credentials import build_connect_source
            from app.services.face_service import face_embedding_cache
            from app.services.system_settings_service import SystemSettingsService

            async with AsyncSessionLocal() as db:
                settings = get_settings()
                from app.services.face_feature_gate import is_face_recognition_allowed

                face_approved = is_face_recognition_allowed(settings)
                face_enabled = False
                if face_approved:
                    try:
                        face_enabled = await SystemSettingsService.effective_bool(
                            db, "enable_face_welcome", settings.enable_face_welcome, store_id=None
                        )
                    except Exception:
                        # 人脸开关读取失败只应关闭人脸预热，不能阻断摄像头恢复。
                        logger.exception("读取人脸功能开关失败；跳过人脸向量缓存预热")
                if face_enabled:
                    # 仅在功能启用且审核/授权通过后加载生物特征缓存。
                    try:
                        n = await face_embedding_cache.warmup(db)
                        logger.info(
                            "启动预热人脸向量缓存 profiles=%s max=%s",
                            n,
                            getattr(face_embedding_cache, "_max_profiles", "?"),
                        )
                    except Exception:
                        logger.exception("启动预热人脸向量缓存失败")
                else:
                    logger.info("跳过人脸向量缓存预热：功能未开启或生产合规/商业授权尚未确认")
                cams = (await db.execute(select(Camera).where(Camera.status != "DISABLED"))).scalars().all()
                mgr = CameraManager()
                for c in cams:
                    src = build_connect_source(c.source_url, c.username, c.password_encrypted)
                    mgr.start_camera(c.id, src, c.fps or 25)
                logger.info("Restored %s cameras", len(cams))
        except Exception as e:
            logger.warning("摄像头自动恢复失败: %s", e)
        # 后台预热 InsightFace + 试听短句 WAV，避免首人/首次试听卡数秒
        _leader_warmup_task = asyncio.create_task(
            _run_leader_scoped(_warmup_recognition_and_tts())
        )

    async def _standby_promote_loop() -> None:
        """auto/leader 非持锁进程持续争抢；Leader 宕机后自动接管调度。"""
        nonlocal is_leader
        attempt = 0
        while True:
            await asyncio.sleep(5)
            if is_leader:
                return
            attempt += 1
            try:
                ok = await try_acquire_leader(_leader_worker_id)
            except Exception:
                logger.exception("standby 抢锁异常 attempt=%s", attempt)
                continue
            if ok:
                # 旧 Leader 的 RTSP open/read 线程最多按 CameraManager 的停止超时排空；
                # 接管方留出略大于该窗口的交接缓冲，避免两进程同时打开同一路摄像头。
                handoff_grace = 13.0
                logger.warning(
                    "standby 已取得 Leader 租约，等待 %.1fs 摄像头交接窗口后再启动工作 worker_id=%s",
                    handoff_grace,
                    _leader_worker_id,
                )
                await asyncio.sleep(handoff_grace)
                from app.core.leader_lock import confirm_leader_owned

                if not await confirm_leader_owned():
                    logger.error("摄像头交接等待期间 Leader 租约丢失，取消本次 promotion")
                    continue
                logger.info(
                    "standby Leader 租约仍有效（attempt=%s role=%s worker_id=%s），启动调度",
                    attempt,
                    role,
                    _leader_worker_id,
                )
                await _start_leader_work()
                return
            if attempt in (1, 6, 24, 120) or attempt % 120 == 0:
                logger.info(
                    "standby 等待 Leader 锁（attempt=%s role=%s worker_id=%s）",
                    attempt,
                    role,
                    _leader_worker_id,
                )

    if is_leader:
        await _start_leader_work()
    else:
        logger.info("PROCESS_ROLE=%s：跳过 scheduler/TTS/camera 恢复", role)
        await _arm_standby_promote()

    yield

    async def _cancel_task_bounded(task: asyncio.Task | None, label: str) -> bool:
        if task is None:
            return True
        if not task.done():
            task.cancel()
            done, _pending = await asyncio.wait({task}, timeout=5.0)
            if task not in done:
                logger.critical("%s 关机时 5 秒内未退出", label)
                return False
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("%s 关机时异常退出", label)
        return True

    # 先停止可能重新晋升的 standby，再停止 Leader 续租；锁必须在工作排空后才释放。
    if _leader_promote_task is not None:
        _leader_promote_task.cancel()
        try:
            await _leader_promote_task
        except (asyncio.CancelledError, Exception):
            pass
        _leader_promote_task = None
    try:
        from app.services.scheduler_admin import stop_overrides_watcher

        await stop_overrides_watcher()
    except Exception:
        logger.exception("关机时停止 overrides watcher 失败")
    jobs_drained = True
    tts_ok = recognition_ok = warmup_ok = camera_ok = True
    if is_leader:
        try:
            from app.services.scheduler_admin import (
                active_leader_job_count,
                cancel_active_leader_jobs,
            )

            await cancel_active_leader_jobs(timeout=5.0)
            jobs_drained = active_leader_job_count() == 0
        except Exception:
            jobs_drained = False
            logger.exception("关机时排空 Leader 定时任务失败")
        try:
            if scheduler.running:
                scheduler.shutdown(wait=False)
        except Exception:
            logger.exception("关机时关闭 scheduler 失败")
        try:
            tts_worker.stop()
        except Exception:
            logger.exception("关机时停止 TTS worker 失败")

        tts_ok = await _cancel_task_bounded(tts_task, "TTS worker")
        recognition_ok = await _cancel_task_bounded(recognition_task, "RecognitionWorker")
        warmup_ok = await _cancel_task_bounded(_leader_warmup_task, "Leader warmup")
        camera_ok = False
        try:
            from camera.camera_manager import CameraManager

            camera_ok = CameraManager().stop_all_cameras()
        except Exception:
            logger.exception("关机时停止摄像头采集失败")

    # 保持 Leader 租约续期直到所有旧工作排空，避免长时间关机清理期间锁提前过期。
    if _leader_stop is not None:
        _leader_stop.set()
    renew_stopped = True
    if _leader_renew_task is not None:
        done, _pending = await asyncio.wait({_leader_renew_task}, timeout=5.0)
        if _leader_renew_task not in done:
            renew_stopped = False
            _leader_renew_task.cancel()
            logger.error("Leader 续租任务关机等待超时，已请求取消")
        else:
            try:
                _leader_renew_task.result()
            except (asyncio.CancelledError, Exception):
                pass

    # renew callback 若正好在关机前触发，可能启动 standby；再次确保其已停止。
    if _leader_promote_task is not None:
        _leader_promote_task.cancel()
        try:
            await _leader_promote_task
        except (asyncio.CancelledError, Exception):
            pass
        _leader_promote_task = None

    if is_leader:
        if jobs_drained and tts_ok and recognition_ok and warmup_ok and camera_ok and renew_stopped:
            from app.core.leader_fencing import deactivate_leader_epoch

            deactivate_leader_epoch(_leader_worker_id)
            if _leader_worker_id:
                await release_leader(_leader_worker_id)
            logger.info("Leader 工作全部排空，已释放 Leader 租约")
        else:
            # 不提前释放租约，避免其他进程在旧任务/摄像头线程仍存活时接管；
            # 进程退出后由 Redis TTL 自然过期。
            logger.critical("Leader 关机排空不完整，保留租约直至 TTL 到期")
    await stop_ws_subscriber()
    await close_http_client()
    # 关闭本进程 Redis 连接池，避免 reload / 测试生命周期内遗留连接。
    try:
        from app.core.redis_client import close_redis

        await close_redis()
    except Exception:
        logger.exception("关闭 Redis 连接池失败")


def create_app() -> FastAPI:
    settings = get_settings()
    # 在模块导入创建 ASGI app 时同步校验，不能依赖 lifespan（可能被 --lifespan off 关闭）。
    validate_settings(settings)
    setup_logging(json_logs=settings.json_logs or settings.is_production, level=settings.log_level)
    # P1-40: 生产环境关闭 /docs /redoc /openapi.json
    docs_url = "/docs" if settings.is_development else None
    redoc_url = "/redoc" if settings.is_development else None
    openapi_url = "/openapi.json" if settings.is_development else None
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # P1-40: Security headers 中间件
    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response

    # P1-40: 危险请求检测中间件（SQL注入 / 路径遍历 / 命令注入 / 漏洞探针）
    @app.middleware("http")
    async def intrusion_detection_middleware(request: Request, call_next):
        is_dangerous, reason = detect_intrusion(request)
        if is_dangerous:
            from fastapi.responses import JSONResponse

            from app.core.database import AsyncSessionLocal
            from app.services.audit_service import AuditService

            try:
                async with AsyncSessionLocal() as audit_db:
                    await AuditService.log_intrusion(
                        audit_db,
                        reason=reason,
                        path=request.url.path,
                        request=request,
                        extra={
                            "method": request.method,
                            "query": (
                                None
                                if request.url.path.startswith("/api/media/private/avatars/")
                                else request.url.query
                            ),
                            "ua": (request.headers.get("user-agent") or "")[:500],
                        },
                    )
                    await audit_db.commit()
            except Exception:
                logger.exception("audit log_intrusion failed: %s", reason)
            from app.core.response import error as api_error

            return JSONResponse(
                status_code=403,
                content=api_error(40301, "Request blocked by security policy", request),
            )
        return await call_next(request)

    # 全局请求体上限与 Nginx client_max_body_size 7m 对齐；回调仍由更严格的 512KB 专项中间件约束。
    from app.core.request_body_limit import RequestBodyLimitMiddleware

    app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=7 * 1024 * 1024)
    # 请求体硬限制放在 Allowlist/RateLimit 之后执行：先拒绝未授权来源，
    # 避免攻击者在读取完整回调体前占用缓冲槽。
    app.add_middleware(WebhookBodyLimitMiddleware, max_body_bytes=settings.webhook_max_body_bytes)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(WebhookAllowlistMiddleware)
    # Task 12：性能指标在 GZip 内层（计时不含压缩开销）；GZip 仅压缩 >1KB JSON
    from app.core.performance_middleware import (
        JsonGZipMiddleware,
        PerformanceMetricsMiddleware,
    )

    app.add_middleware(PerformanceMetricsMiddleware)
    app.add_middleware(JsonGZipMiddleware)
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    from sqlalchemy.exc import IntegrityError, OperationalError

    app.add_exception_handler(IntegrityError, sqlalchemy_integrity_handler)
    app.add_exception_handler(OperationalError, sqlalchemy_operational_handler)
    # P0-5：兜底异常处理器，确保所有未捕获异常返回 JSON 而非 HTML 500
    app.add_exception_handler(Exception, generic_exception_handler)
    app.include_router(api_router)

    # 无前缀探针：与 /api/health、/api/ready 同一 handler（独立 router，避免同对象重复注册）
    app.include_router(health_root_router)

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    # Windows / 部分 Linux 的 mimetypes 不含 webp，StaticFiles 会落成
    # application/octet-stream，微信/支付宝 <image> 将拒绝展示。
    import mimetypes

    mimetypes.add_type("image/webp", ".webp")

    # 头像属于顾客个人资料；旧版本地 /uploads/avatars URL 也必须经过短时签名媒体路由。
    # 显式路由放在 /uploads StaticFiles mount 之前，避免开发环境绕过签名保护。
    @app.get("/uploads/avatars/{key:path}", include_in_schema=False)
    async def _deny_public_customer_avatar(key: str):
        raise AppException(40425, "顾客头像不允许通过公开静态路径访问", status_code=404)

    # 开发环境静态挂载也必须拦截访客人脸抓拍，不能因绕过 Nginx 而公开。
    @app.get("/uploads/visitors/{key:path}", include_in_schema=False)
    async def _deny_public_visitor_snapshot(key: str):
        raise AppException(40425, "访客抓拍不允许通过公开静态路径访问", status_code=404)

    if settings.is_development:
        app.mount("/uploads", StaticFiles(directory=str(upload_dir)), name="uploads")

    return app


app = create_app()

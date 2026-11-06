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
from app.core.logging_config import setup_logging
from app.core.rate_limit import RateLimitMiddleware
from app.core.redis_client import RequestIDMiddleware, close_redis
from app.core.startup_checks import validate_settings
from app.services.face_service import WelcomeService
from app.services.websocket_manager import on_redis_ws_message
from app.services.ws_pubsub import start_ws_subscriber, stop_ws_subscriber
from app.tasks.scheduler import register_tasks
from app.workers.tts_worker import TTSWorker

logger = logging.getLogger(__name__)
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
_leader_stop: asyncio.Event | None = None
_leader_renew_task: asyncio.Task | None = None
_leader_worker_id: str | None = None


async def _warmup_recognition_and_tts() -> None:
    """后台预热：先 InsightFace live（门口识别），再 TTS/G.711。

    旧顺序「先 TTS 再人脸」会让 RecognitionWorker 空跑 40s+ 漏检，体感像「认出访客要等十几秒」。
    """
    # 1) 实时人脸模型优先，缩短「人到→检出」
    try:

        def _load_live() -> None:
            from app.services.face_extractor import _face_app_live

            _face_app_live()

        await asyncio.to_thread(_load_live)
        logger.info("InsightFace live 预热完成")
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
    warmup_task: asyncio.Task | None = None
    validate_settings(settings)

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
            # 显式 leader：热重载时常被已死进程的 TTL 锁挡住，强制接管保证 TTS/识别起来
            logger.warning(
                "PROCESS_ROLE=leader 未抢到锁，强制接管 Redis Leader（worker_id=%s）",
                _leader_worker_id,
            )
            try:
                from app.core.leader_lock import LEADER_KEY
                from app.core.redis_client import get_redis

                r = await get_redis()
                await r.delete(LEADER_KEY)
            except Exception:
                logger.exception("强制删除 Leader 锁失败")
            is_leader = await try_acquire_leader(_leader_worker_id)
            if not is_leader:
                logger.warning(
                    "PROCESS_ROLE=leader 强制接管后仍失败（worker_id=%s），稍后后台重试",
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
        """锁丢失：立即停调度与后台 worker，避免双 Leader；随后重新 standby 争抢。"""
        nonlocal is_leader
        logger.warning("执行 Leader 降级：shutdown scheduler / TTS / RecognitionWorker")
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
            pass
        if tts_task and not tts_task.done():
            tts_task.cancel()
        if recognition_task and not recognition_task.done():
            recognition_task.cancel()
        is_leader = False
        # gunicorn 其他 worker / 本进程降级后继续争抢，避免调度永久空窗
        await _arm_standby_promote()

    async def _start_leader_work() -> None:
        """抢到锁后启动调度器 / TTS / 识别 / 摄像头。"""
        nonlocal is_leader, _leader_promote_task
        global tts_task, recognition_task, _leader_stop, _leader_renew_task
        from app.core.leader_lock import is_leader_held, set_leader_held, set_on_leader_lost

        # 成为 Leader 后停掉 standby 争抢
        if _leader_promote_task is not None and not _leader_promote_task.done():
            _leader_promote_task.cancel()
            try:
                await _leader_promote_task
            except (asyncio.CancelledError, Exception):
                pass
            _leader_promote_task = None

        is_leader = True
        set_leader_held(True)
        set_on_leader_lost(_stop_leader_work)
        # P0-6: 注入 Leader 检查回调到 CameraManager
        from camera.camera_manager import CameraManager

        CameraManager.set_leader_check(is_leader_held)
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
        tts_task = asyncio.create_task(tts_worker.run())
        logger.info(
            "TTS worker started (leader) provider=%s",
            getattr(settings, "tts_provider", "?"),
        )
        if settings.enable_recognition_worker:
            try:
                from app.workers.recognition_worker import RecognitionWorker

                recognition_task = asyncio.create_task(RecognitionWorker().run())
                logger.info("RecognitionWorker started (leader)")
            except Exception as e:
                logger.warning("RecognitionWorker 未启动: %s", e)
        try:
            from sqlalchemy import select

            from app.core.database import AsyncSessionLocal
            from app.models import Camera
            from app.services.camera_credentials import build_connect_source
            from app.services.face_service import face_embedding_cache

            async with AsyncSessionLocal() as db:
                # 启动即加载授权会员人脸，保证识别时效（增量刷新仍走缓存）
                try:
                    n = await face_embedding_cache.warmup(db)
                    logger.info(
                        "启动预热人脸向量缓存 profiles=%s max=%s",
                        n,
                        getattr(face_embedding_cache, "_max_profiles", "?"),
                    )
                except Exception:
                    logger.exception("启动预热人脸向量缓存失败")
                cams = (await db.execute(select(Camera).where(Camera.status != "DISABLED"))).scalars().all()
                mgr = CameraManager()
                for c in cams:
                    src = build_connect_source(c.source_url, c.username, c.password_encrypted)
                    mgr.start_camera(c.id, src, c.fps or 25)
                logger.info("Restored %s cameras", len(cams))
        except Exception as e:
            logger.warning("摄像头自动恢复失败: %s", e)
        # 后台预热 InsightFace + 试听短句 WAV，避免首人/首次试听卡数秒
        warmup_task = asyncio.create_task(_warmup_recognition_and_tts())

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
                logger.info(
                    "standby 抢锁成功（attempt=%s role=%s worker_id=%s），启动调度",
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

    if _leader_promote_task is not None:
        _leader_promote_task.cancel()
        try:
            await _leader_promote_task
        except (asyncio.CancelledError, Exception):
            pass
    try:
        from app.services.scheduler_admin import stop_overrides_watcher

        await stop_overrides_watcher()
    except Exception:
        pass
    await stop_ws_subscriber()
    await close_http_client()
    if _leader_stop is not None:
        _leader_stop.set()
    if _leader_renew_task is not None:
        _leader_renew_task.cancel()
        try:
            await _leader_renew_task
        except (asyncio.CancelledError, Exception):
            pass
    if is_leader and _leader_worker_id:
        await release_leader(_leader_worker_id)
    if is_leader:
        tts_worker.stop()
        if tts_task:
            tts_task.cancel()
            try:
                await tts_task
            except asyncio.CancelledError:
                pass
        if recognition_task:
            recognition_task.cancel()
            try:
                await recognition_task
            except (asyncio.CancelledError, Exception):
                pass
        scheduler.shutdown()
    if warmup_task is not None and not warmup_task.done():
        warmup_task.cancel()
        try:
            await warmup_task
        except asyncio.CancelledError:
            pass
    # 所有已知后台 Redis 使用者停止后，再释放本进程连接池。
    await close_redis()


def create_app() -> FastAPI:
    settings = get_settings()
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
        # P1-43: Webhook body size limit — 超限返回 413
        path = request.url.path
        if "/webhook" in path or "/notify" in path or "/callback" in path:
            cl = request.headers.get("content-length")
            if cl and int(cl) > settings.webhook_max_body_bytes:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=413,
                    content={"code": 413, "message": "Webhook body too large"},
                )
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
                            "query": request.url.query,
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
    if settings.is_development:
        app.mount("/uploads", StaticFiles(directory=str(upload_dir)), name="uploads")

    return app


app = create_app()

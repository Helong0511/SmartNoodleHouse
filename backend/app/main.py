import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import app.bootstrap_path  # noqa: F401 — 项目根目录加入 sys.path（camera 模块）

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.exceptions import (
    AppException,
    app_exception_handler,
    http_exception_handler,
    sqlalchemy_integrity_handler,
    sqlalchemy_operational_handler,
)
from app.core.http_client import close_http_client
from app.core.intrusion_detection import detect_intrusion
from app.core.ip_allowlist import WebhookAllowlistMiddleware
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
scheduler = AsyncIOScheduler()
tts_worker = TTSWorker()
tts_task: asyncio.Task | None = None
recognition_task: asyncio.Task | None = None
_leader_stop: asyncio.Event | None = None
_leader_renew_task: asyncio.Task | None = None
_leader_worker_id: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tts_task, recognition_task, _leader_stop, _leader_renew_task, _leader_worker_id
    settings = get_settings()
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
            await db.commit()
            await SystemSettingsService._load_raw(db, force=True)
        logger.info("system_settings 已种子并预热缓存")
    except Exception as e:
        logger.warning("system_settings 种子失败（可稍后由后台页触发）: %s", e)

    # 必须走 settings（会读 .env）；勿用 os.getenv，否则仅写在 .env 的 PROCESS_ROLE 会被忽略
    from app.core.leader_lock import make_worker_id, release_leader, renew_leader_loop, try_acquire_leader

    role = (settings.process_role or "auto").strip().lower()
    _leader_worker_id = make_worker_id()
    if role == "worker":
        is_leader = False
    elif role in ("leader", "auto"):
        is_leader = await try_acquire_leader(_leader_worker_id)
        if role == "leader" and not is_leader:
            logger.warning(
                "PROCESS_ROLE=leader 但未抢到 Redis Leader 锁（worker_id=%s），本进程不启动调度以防重复",
                _leader_worker_id,
            )
    else:
        logger.warning("未知 PROCESS_ROLE=%s，按 worker 处理", role)
        is_leader = False

    await start_ws_subscriber(on_redis_ws_message)

    async def _stop_leader_work() -> None:
        """锁丢失：立即停调度与后台 worker，避免双 Leader。"""
        nonlocal is_leader
        logger.warning("执行 Leader 降级：shutdown scheduler / TTS / RecognitionWorker")
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

    if is_leader:
        from app.core.leader_lock import is_leader_held, set_leader_held, set_on_leader_lost

        set_leader_held(True)
        set_on_leader_lost(_stop_leader_work)
        # P0-6: 注入 Leader 检查回调到 CameraManager
        from camera.camera_manager import CameraManager

        CameraManager.set_leader_check(is_leader_held)
        _leader_stop = asyncio.Event()
        _leader_renew_task = asyncio.create_task(renew_leader_loop(_leader_worker_id, _leader_stop))
        register_tasks(scheduler)
        scheduler.start()
        logger.info("Scheduler started (leader worker_id=%s role=%s)", _leader_worker_id, role)
        if settings.tts_provider == "local":
            tts_task = asyncio.create_task(tts_worker.run(WelcomeService.get_queue()))
            logger.info("TTS worker started (leader)")
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

            async with AsyncSessionLocal() as db:
                cams = (await db.execute(select(Camera).where(Camera.status != "DISABLED"))).scalars().all()
                mgr = CameraManager()
                for c in cams:
                    src = build_connect_source(c.source_url, c.username, c.password_encrypted)
                    mgr.start_camera(c.id, src, c.fps or 25)
                logger.info("Restored %s cameras", len(cams))
        except Exception as e:
            logger.warning("摄像头自动恢复失败: %s", e)
    else:
        logger.info("PROCESS_ROLE=%s：跳过 scheduler/TTS/camera 恢复", role)

    yield

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
            return JSONResponse(
                status_code=403,
                content={"code": 403, "message": "Request blocked by security policy"},
            )
        return await call_next(request)

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(WebhookAllowlistMiddleware)
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    from sqlalchemy.exc import IntegrityError, OperationalError

    app.add_exception_handler(IntegrityError, sqlalchemy_integrity_handler)
    app.add_exception_handler(OperationalError, sqlalchemy_operational_handler)
    app.include_router(api_router)

    # 无前缀探针：与 /api/v1/health、/api/v1/ready 同源（DB+Redis）
    from app.api.v1 import health as health_routes

    app.include_router(health_routes.router)

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    if settings.is_development:
        app.mount("/uploads", StaticFiles(directory=str(upload_dir)), name="uploads")

    return app


app = create_app()

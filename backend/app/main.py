import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import app.bootstrap_path  # noqa: F401 — 项目根目录加入 sys.path（camera 模块）

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tts_task, recognition_task
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

    # 必须走 settings（会读 .env）；勿用 os.getenv，否则仅写在 .env 的 PROCESS_ROLE 会被忽略
    role = (settings.process_role or "leader").strip().lower()
    is_leader = role == "leader"
    await start_ws_subscriber(on_redis_ws_message)

    if is_leader:
        register_tasks(scheduler)
        scheduler.start()
        logger.info("Scheduler started (leader)")
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
            from camera.camera_manager import CameraManager

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
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(WebhookAllowlistMiddleware)
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    from sqlalchemy.exc import IntegrityError, OperationalError

    app.add_exception_handler(IntegrityError, sqlalchemy_integrity_handler)
    app.add_exception_handler(OperationalError, sqlalchemy_operational_handler)
    app.include_router(api_router)

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    if settings.is_development:
        app.mount("/uploads", StaticFiles(directory=str(upload_dir)), name="uploads")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()

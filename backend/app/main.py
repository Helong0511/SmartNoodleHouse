import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.exceptions import AppException, app_exception_handler, http_exception_handler
from app.core.redis_client import RequestIDMiddleware
from app.services.face_service import WelcomeService
from app.tasks.scheduler import register_tasks
from app.workers.tts_worker import TTSWorker

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()
tts_worker = TTSWorker()
tts_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tts_task
    register_tasks(scheduler)
    scheduler.start()
    logger.info("Scheduler started")

    settings = get_settings()
    if settings.tts_provider == "local":
        tts_task = asyncio.create_task(tts_worker.run(WelcomeService.get_queue()))
        logger.info("TTS worker started")

    yield

    tts_worker.stop()
    if tts_task:
        tts_task.cancel()
        try:
            await tts_task
        except asyncio.CancelledError:
            pass
    scheduler.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.include_router(api_router)

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(upload_dir)), name="uploads")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()

import cv2
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.response import success
from app.models import Camera, VisitorEvent
from app.services.face_service import FaceService, VisitorService

router = APIRouter(prefix="/cameras", tags=["摄像头"])


@router.get("", dependencies=[require_permission("camera:read")])
async def list_cameras(request: Request, db: DbSession, user: CurrentUser):
    result = await db.execute(select(Camera))
    from camera.camera_manager import CameraManager
    from app.services.camera_credentials import mask_rtsp_url

    mgr = CameraManager()
    items = []
    for c in result.scalars():
        items.append({
            "id": c.id,
            "name": c.name,
            "camera_type": c.camera_type,
            "source_url": mask_rtsp_url(c.source_url),
            "status": mgr.get_status(c.id) if mgr.get_worker(c.id) else c.status,
            "fps": c.fps,
            "has_password": bool(c.password_encrypted),
        })
    return success(items, request=request)


@router.post("", dependencies=[require_permission("camera:manage")])
async def create_camera(
    request: Request,
    db: DbSession,
    store_id: int = 1,
    name: str = "",
    camera_type: str = "USB",
    source_url: str = "0",
    username: str | None = None,
    password: str | None = None,
    fps: int = 25,
):
    from app.services.camera_credentials import encrypt_password

    pwd_enc = encrypt_password(password) if password else None
    camera = Camera(
        store_id=store_id,
        name=name,
        camera_type=camera_type,
        source_url=source_url,
        username=username,
        password_encrypted=pwd_enc,
        fps=fps,
    )
    db.add(camera)
    await db.flush()
    from camera.camera_manager import CameraManager

    CameraManager().start_camera(camera.id, source_url, fps)
    return success({"id": camera.id}, request=request)


@router.get("/{camera_id}/stream")
async def camera_stream(camera_id: int):
    from camera.camera_manager import CameraManager

    mgr = CameraManager()
    worker = mgr.get_worker(camera_id)

    def generate():
        while True:
            if worker:
                frame, _ = worker.frame_buffer.get_frame()
                if frame is not None:
                    _, jpeg = cv2.imencode(".jpg", frame)
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
            import time
            time.sleep(0.04)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.get("/visitors/stats")
async def visitor_stats(request: Request, db: DbSession, camera_id: int | None = None):
    q = select(func.count()).select_from(VisitorEvent)
    if camera_id:
        q = q.where(VisitorEvent.camera_id == camera_id)
    total = await db.scalar(q) or 0
    return success({"total_visitors": total}, request=request)

"""摄像头 API：鉴权 + 解密密码连接 + 生命周期"""
from __future__ import annotations

import cv2
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.response import success
from app.models import Camera, VisitorEvent
from app.services.camera_credentials import (
    build_connect_source,
    encrypt_password,
    mask_rtsp_url,
)

router = APIRouter(prefix="/cameras", tags=["摄像头"])


def _start_worker(camera: Camera) -> None:
    from camera.camera_manager import CameraManager

    source = build_connect_source(camera.source_url, camera.username, camera.password_encrypted)
    CameraManager().start_camera(camera.id, source, camera.fps or 25)


@router.get("", dependencies=[require_permission("camera:read")])
async def list_cameras(request: Request, db: DbSession, user: CurrentUser):
    q = select(Camera)
    if user.store_id and user.role.code != "SUPER_ADMIN":
        q = q.where(Camera.store_id == user.store_id)
    result = await db.execute(q)
    from camera.camera_manager import CameraManager

    mgr = CameraManager()
    items = []
    for c in result.scalars():
        items.append(
            {
                "id": c.id,
                "name": c.name,
                "camera_type": c.camera_type,
                "source_url": mask_rtsp_url(c.source_url),
                "status": mgr.get_status(c.id) if mgr.get_worker(c.id) else c.status,
                "fps": c.fps,
                "store_id": c.store_id,
                "has_password": bool(c.password_encrypted),
            }
        )
    return success(items, request=request)


@router.post("", dependencies=[require_permission("camera:manage")])
async def create_camera(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
    name: str = "",
    camera_type: str = "USB",
    source_url: str = "0",
    username: str | None = None,
    password: str | None = None,
    fps: int = 25,
):
    from app.services.store_scope import assert_store_access, effective_store_id

    sid = effective_store_id(user, store_id) or user.store_id
    if not sid or int(sid) <= 0:
        return {"code": 400, "message": "store_id 非法或账号未绑定门店", "data": None}
    assert_store_access(user, sid)
    pwd_enc = encrypt_password(password) if password else None
    # source_url 入库时去掉明文密码
    safe_url = mask_rtsp_url(source_url) if password and "://" in source_url else source_url
    camera = Camera(
        store_id=int(sid),
        name=name,
        camera_type=camera_type,
        source_url=safe_url if password else source_url,
        username=username,
        password_encrypted=pwd_enc,
        fps=fps,
        status="OFFLINE",
    )
    db.add(camera)
    await db.flush()
    _start_worker(camera)
    camera.status = "ONLINE"
    return success({"id": camera.id}, request=request)


@router.post("/{camera_id}/start", dependencies=[require_permission("camera:manage")])
async def start_camera(camera_id: int, request: Request, db: DbSession, user: CurrentUser):
    camera = await db.get(Camera, camera_id)
    if not camera:
        return {"code": 404, "message": "摄像头不存在", "data": None}
    if user.store_id and user.role.code != "SUPER_ADMIN" and camera.store_id != user.store_id:
        return {"code": 403, "message": "无权操作该门店摄像头", "data": None}
    _start_worker(camera)
    camera.status = "ONLINE"
    return success({"id": camera.id, "status": "ONLINE"}, request=request)


@router.post("/{camera_id}/stop", dependencies=[require_permission("camera:manage")])
async def stop_camera(camera_id: int, request: Request, db: DbSession, user: CurrentUser):
    from camera.camera_manager import CameraManager

    camera = await db.get(Camera, camera_id)
    if not camera:
        return {"code": 404, "message": "摄像头不存在", "data": None}
    CameraManager().stop_camera(camera_id)
    camera.status = "OFFLINE"
    return success({"id": camera.id, "status": "OFFLINE"}, request=request)


@router.post("/{camera_id}/stream-ticket", dependencies=[require_permission("camera:read")])
async def create_camera_stream_ticket(camera_id: int, request: Request, db: DbSession, user: CurrentUser):
    """签发 2 分钟流媒体票据，供 <img src> 使用，避免 JWT 长期暴露在 query。"""
    from app.core.security import create_stream_ticket

    camera = await db.get(Camera, camera_id)
    if not camera:
        return {"code": 404, "message": "摄像头不存在", "data": None}
    if user.store_id and user.role.code != "SUPER_ADMIN" and camera.store_id != user.store_id:
        return {"code": 403, "message": "无权查看该门店摄像头", "data": None}
    ticket = create_stream_ticket(user.id, camera_id, token_version=int(getattr(user, "token_version", 0) or 0))
    return success({"ticket": ticket, "expires_in": 120}, request=request)


@router.get("/{camera_id}/stream")
async def camera_stream(
    camera_id: int,
    request: Request,
    db: DbSession,
    access_token: str | None = None,
    ticket: str | None = None,
):
    """MJPEG 流：Authorization Bearer / 短期 ticket（推荐）/ access_token（兼容）。"""
    from camera.camera_manager import CameraManager
    from app.core.security import decode_token
    from jose import JWTError
    from sqlalchemy.orm import selectinload
    from app.models import User

    token = None
    auth = request.headers.get("authorization") or ""
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1]
    elif ticket:
        token = ticket
    elif access_token:
        token = access_token
    if not token:
        return {"code": 401, "message": "未登录", "data": None}
    try:
        payload = decode_token(token)
        typ = payload.get("typ")
        if typ not in ("staff", "stream"):
            return {"code": 401, "message": "请使用员工账号或流票据", "data": None}
        if typ == "stream" and int(payload.get("cam") or 0) != int(camera_id):
            return {"code": 403, "message": "票据与摄像头不匹配", "data": None}
        user = (
            await db.execute(
                select(User).options(selectinload(User.role)).where(User.id == int(payload["sub"]), User.status == 1)
            )
        ).scalar_one_or_none()
        if not user:
            return {"code": 401, "message": "用户不存在", "data": None}
        if int(payload.get("ver") or 0) != int(getattr(user, "token_version", 0) or 0):
            return {"code": 401, "message": "令牌已失效", "data": None}
        perms_ok = user.role.code == "SUPER_ADMIN"
        if not perms_ok:
            from app.api.deps import get_user_permissions

            perms = await get_user_permissions(db, user.role_id)
            perms_ok = "camera:read" in perms
        if not perms_ok:
            return {"code": 403, "message": "缺少权限: camera:read", "data": None}
    except (JWTError, KeyError, ValueError):
        return {"code": 401, "message": "无效令牌", "data": None}

    camera = await db.get(Camera, camera_id)
    if not camera:
        return {"code": 404, "message": "摄像头不存在", "data": None}
    if user.store_id and user.role.code != "SUPER_ADMIN" and camera.store_id != user.store_id:
        return {"code": 403, "message": "无权查看该门店摄像头", "data": None}

    mgr = CameraManager()
    worker = mgr.get_worker(camera_id)
    if not worker:
        _start_worker(camera)
        worker = mgr.get_worker(camera_id)

    def generate():
        import time

        while True:
            if worker:
                frame, _ = worker.frame_buffer.get_frame()
                if frame is not None:
                    _, jpeg = cv2.imencode(".jpg", frame)
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
            time.sleep(max(0.04, 1.0 / max((worker.fps if worker else 25), 1)))

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.get("/visitors/stats", dependencies=[require_permission("camera:read")])
async def visitor_stats(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    camera_id: int | None = None,
):
    q = select(func.count()).select_from(VisitorEvent)
    if camera_id:
        camera = await db.get(Camera, camera_id)
        if not camera:
            return {"code": 404, "message": "摄像头不存在", "data": None}
        if user.store_id and user.role.code != "SUPER_ADMIN" and camera.store_id != user.store_id:
            return {"code": 403, "message": "无权查看", "data": None}
        q = q.where(VisitorEvent.camera_id == camera_id)
    elif user.store_id and user.role.code != "SUPER_ADMIN":
        cam_ids = (await db.execute(select(Camera.id).where(Camera.store_id == user.store_id))).scalars().all()
        q = q.where(VisitorEvent.camera_id.in_(cam_ids or [-1]))
    total = await db.scalar(q) or 0
    return success({"total_visitors": total}, request=request)

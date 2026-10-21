"""摄像头 API：鉴权 + 解密密码连接 + 生命周期 + 访客统计"""
from __future__ import annotations

import asyncio
import time

import cv2
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.orm_safe import orm_attr_loaded
from app.core.response import now_local, success, serialize_dt
from app.models import Camera, VisitorEvent
from app.services.audit_service import AuditService
from app.services.camera_credentials import (
    build_connect_source,
    encrypt_password,
    mask_rtsp_url,
    prepare_camera_source,
)
from app.services.store_scope import assert_store_access, require_bound_store

router = APIRouter(prefix="/cameras", tags=["摄像头"])


class CameraCreate(BaseModel):
    store_id: int | None = None
    name: str = Field(..., min_length=1, max_length=100)
    camera_type: str = Field(default="USB", pattern="^(USB|RTSP)$")
    source_url: str = Field(..., min_length=1, max_length=500)
    username: str | None = Field(default=None, max_length=100)
    password: str | None = None
    fps: int = Field(default=25, ge=1, le=60)
    detect_fps: int = Field(default=3, ge=1, le=30)
    auto_start: bool = True


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    camera_type: str | None = Field(default=None, pattern="^(USB|RTSP)$")
    source_url: str | None = Field(default=None, min_length=1, max_length=500)
    username: str | None = Field(default=None, max_length=100)
    password: str | None = None
    fps: int | None = Field(default=None, ge=1, le=60)
    detect_fps: int | None = Field(default=None, ge=1, le=30)
    clear_password: bool = False


def _assert_cam_access(user, camera: Camera) -> None:
    assert_store_access(user, camera.store_id)


def _start_worker(camera: Camera, *, force: bool = False) -> None:
    from camera.camera_manager import CameraManager

    source = build_connect_source(camera.source_url, camera.username, camera.password_encrypted)
    CameraManager().start_camera(camera.id, source, camera.fps or 25, force=force)


def _serialize(c: Camera, mgr) -> dict:
    runtime = mgr.get_runtime(c.id) if mgr else {"running": False, "status": c.status, "has_frame": False}
    live = runtime.get("status") if runtime.get("running") else c.status
    return {
        "id": c.id,
        "name": c.name,
        "camera_type": c.camera_type,
        "source_url": mask_rtsp_url(c.source_url),
        "username": c.username,
        "status": live or c.status,
        "fps": c.fps,
        "detect_fps": c.detect_fps,
        "store_id": c.store_id,
        "has_password": bool(c.password_encrypted),
        "last_online_at": serialize_dt(c.last_online_at),
        "runtime": runtime,
        # getattr 对 mapped 列仍会懒加载；未 refresh 时用 orm_attr_loaded 避免 MissingGreenlet
        "created_at": serialize_dt(c.created_at) if orm_attr_loaded(c, "created_at") else None,
    }


@router.get("/overview", dependencies=[require_permission("camera:read")])
async def cameras_overview(request: Request, db: DbSession, user: CurrentUser):
    from camera.camera_manager import CameraManager

    q = select(Camera)
    sid = require_bound_store(user)
    if sid is not None:
        q = q.where(Camera.store_id == sid)
    cams = list((await db.execute(q)).scalars().all())
    mgr = CameraManager()
    online = 0
    reconnecting = 0
    for c in cams:
        st = mgr.get_status(c.id) if mgr.get_worker(c.id) else c.status
        if st == "ONLINE":
            online += 1
        elif st == "RECONNECTING":
            reconnecting += 1

    today = now_local().replace(hour=0, minute=0, second=0, microsecond=0)
    cam_ids = [c.id for c in cams] or [-1]
    visitor_today = int(
        await db.scalar(
            select(func.count())
            .select_from(VisitorEvent)
            .where(VisitorEvent.camera_id.in_(cam_ids), VisitorEvent.event_time >= today)
        )
        or 0
    )
    visitor_total = int(
        await db.scalar(select(func.count()).select_from(VisitorEvent).where(VisitorEvent.camera_id.in_(cam_ids)))
        or 0
    )
    return success(
        {
            "camera_count": len(cams),
            "online": online,
            "reconnecting": reconnecting,
            "offline": max(0, len(cams) - online - reconnecting),
            "visitor_today": visitor_today,
            "visitor_total": visitor_total,
        },
        request=request,
    )


@router.get("", dependencies=[require_permission("camera:read")])
async def list_cameras(request: Request, db: DbSession, user: CurrentUser, store_id: int | None = None):
    q = select(Camera).order_by(Camera.id.desc())
    # 超管可按门店筛选摄像头（迎宾日志的门店→摄像头联动）；非超管强制本店
    sid = require_bound_store(user, store_id)
    if sid is not None:
        q = q.where(Camera.store_id == sid)
    result = await db.execute(q)
    from camera.camera_manager import CameraManager

    mgr = CameraManager()
    items = [_serialize(c, mgr) for c in result.scalars()]
    return success({"items": items, "total": len(items)}, request=request)


@router.get("/visitors/events", dependencies=[require_permission("camera:read")])
async def visitor_events(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    camera_id: int | None = None,
    store_id: int | None = None,
    event_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    from sqlalchemy import distinct

    from app.core.response import apply_date_range, calc_pages

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = select(VisitorEvent)
    if camera_id:
        camera = await db.get(Camera, camera_id)
        if not camera:
            raise AppException(40420, "摄像头不存在")
        _assert_cam_access(user, camera)
        q = q.where(VisitorEvent.camera_id == camera_id)
    else:
        sid = require_bound_store(user, store_id)
        if sid is not None:
            cam_ids = (await db.execute(select(Camera.id).where(Camera.store_id == sid))).scalars().all()
            q = q.where(VisitorEvent.camera_id.in_(list(cam_ids) or [-1]))
    q = apply_date_range(q, VisitorEvent.event_time, date_from, date_to)
    if event_type and event_type.strip():
        q = q.where(VisitorEvent.event_type == event_type.strip().upper())

    # 事件类型开放集合（当前为 ENTER 等），返回去重列表供前端下拉
    type_rows = (
        await db.execute(
            select(distinct(VisitorEvent.event_type))
            .where(VisitorEvent.event_type.isnot(None))
            .order_by(VisitorEvent.event_type)
            .limit(100)
        )
    ).scalars().all()
    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = list(
        (
            await db.execute(
                q.order_by(VisitorEvent.event_time.desc(), VisitorEvent.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
    )
    items = [
        {
            "id": e.id,
            "visitor_no": e.visitor_no,
            "camera_id": e.camera_id,
            "event_type": e.event_type,
            "event_time": serialize_dt(e.event_time),
            "expires_at": serialize_dt(e.expires_at),
            "snapshot_path": e.snapshot_path,
        }
        for e in rows
    ]
    return success(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
            "event_types": [t for t in type_rows if t],
        },
        request=request,
    )


@router.post("", dependencies=[require_permission("camera:manage")])
async def create_camera(data: CameraCreate, request: Request, db: DbSession, user: CurrentUser):
    # 超管可传 store_id；非超管强制本店。超管未传且无绑店则要求指定门店
    sid = require_bound_store(user, data.store_id)
    if sid is None:
        if user.store_id:
            sid = int(user.store_id)
        else:
            raise AppException(40020, "请指定门店后再创建摄像头")
    assert_store_access(user, sid)
    safe_url, user_name, plain_pwd = prepare_camera_source(
        data.source_url, username=data.username, password=data.password
    )
    pwd_enc = encrypt_password(plain_pwd) if plain_pwd else None
    camera = Camera(
        store_id=int(sid),
        name=data.name.strip(),
        camera_type=data.camera_type,
        source_url=safe_url,
        username=user_name,
        password_encrypted=pwd_enc,
        fps=data.fps,
        detect_fps=data.detect_fps,
        status="OFFLINE",
    )
    db.add(camera)
    await db.flush()
    # server_default 的 created_at 须 refresh，否则同步 _serialize 会 MissingGreenlet
    await db.refresh(camera)
    await AuditService.log(
        db,
        user_id=user.id,
        action="camera.create",
        module="camera",
        target_type="camera",
        target_id=camera.id,
        detail={
            "store_id": sid,
            "name": camera.name,
            "camera_type": camera.camera_type,
            "auto_start": bool(data.auto_start),
        },
        request=request,
    )
    if data.auto_start:
        _start_worker(camera, force=True)
        await asyncio.sleep(0.3)
        from camera.camera_manager import CameraManager

        mgr = CameraManager()
        st = mgr.get_status(camera.id)
        camera.status = st if st else "OFFLINE"
        if camera.status == "ONLINE":
            camera.last_online_at = now_local()
        return success(_serialize(camera, mgr), request=request)
    from camera.camera_manager import CameraManager

    return success(_serialize(camera, CameraManager()), request=request)


@router.put("/{camera_id}", dependencies=[require_permission("camera:manage")])
async def update_camera(camera_id: int, data: CameraUpdate, request: Request, db: DbSession, user: CurrentUser):
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise AppException(40420, "摄像头不存在")
    _assert_cam_access(user, camera)
    payload = data.model_dump(exclude_unset=True)
    restart = False
    if "name" in payload and payload["name"]:
        camera.name = payload["name"].strip()
    if "camera_type" in payload and payload["camera_type"]:
        camera.camera_type = payload["camera_type"]
        restart = True
    # source_url / username / password 统一走分离存储，禁止明文口令入库
    touch_source = "source_url" in payload or "username" in payload or "password" in payload or payload.get("clear_password")
    if touch_source:
        next_url = payload["source_url"] if "source_url" in payload and payload["source_url"] else camera.source_url
        next_user = payload["username"] if "username" in payload else camera.username
        next_pwd = None
        if payload.get("clear_password"):
            camera.password_encrypted = None
        elif payload.get("password"):
            next_pwd = payload["password"]
        safe_url, user_name, plain_pwd = prepare_camera_source(
            next_url or "",
            username=next_user,
            password=next_pwd,
        )
        camera.source_url = safe_url
        if "username" in payload or plain_pwd or user_name:
            camera.username = user_name
        if plain_pwd:
            camera.password_encrypted = encrypt_password(plain_pwd)
        elif "source_url" in payload and plain_pwd is None and not payload.get("password") and not payload.get("clear_password"):
            # 仅改 URL 时若 URL 内嵌了密码，prepare 已拆出 plain_pwd；否则保留原加密口令
            pass
        restart = True
    if "fps" in payload and payload["fps"] is not None:
        camera.fps = payload["fps"]
        restart = True
    if "detect_fps" in payload and payload["detect_fps"] is not None:
        camera.detect_fps = payload["detect_fps"]
    await db.flush()
    from camera.camera_manager import CameraManager

    mgr = CameraManager()
    if restart and mgr.get_worker(camera_id):
        _start_worker(camera, force=True)
        camera.status = mgr.get_status(camera_id) or "OFFLINE"
    await AuditService.log(
        db,
        user_id=user.id,
        action="camera.update",
        module="camera",
        target_type="camera",
        target_id=camera.id,
        detail={
            "store_id": camera.store_id,
            "changes": {k: v for k, v in payload.items() if k not in ("password",)},
            "restart": restart,
        },
        request=request,
    )
    return success(_serialize(camera, mgr), request=request)


@router.delete("/{camera_id}", dependencies=[require_permission("camera:manage")])
async def delete_camera(camera_id: int, request: Request, db: DbSession, user: CurrentUser):
    from camera.camera_manager import CameraManager

    camera = await db.get(Camera, camera_id)
    if not camera:
        raise AppException(40420, "摄像头不存在")
    _assert_cam_access(user, camera)
    store_id = camera.store_id
    name = camera.name
    CameraManager().stop_camera(camera_id)
    await db.delete(camera)
    await db.flush()
    await AuditService.log(
        db,
        user_id=user.id,
        action="camera.delete",
        module="camera",
        target_type="camera",
        target_id=camera_id,
        detail={"store_id": store_id, "name": name},
        request=request,
    )
    return success({"deleted": True, "id": camera_id}, request=request)


@router.post("/{camera_id}/start", dependencies=[require_permission("camera:manage")])
async def start_camera(camera_id: int, request: Request, db: DbSession, user: CurrentUser):
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise AppException(40420, "摄像头不存在")
    _assert_cam_access(user, camera)
    _start_worker(camera, force=True)
    await asyncio.sleep(0.3)
    from camera.camera_manager import CameraManager

    st = CameraManager().get_status(camera_id) or "OFFLINE"
    camera.status = st
    if st == "ONLINE":
        camera.last_online_at = now_local()
    await AuditService.log(
        db,
        user_id=user.id,
        action="camera.start",
        module="camera",
        target_type="camera",
        target_id=camera.id,
        detail={"store_id": camera.store_id, "status": st},
        request=request,
    )
    return success(_serialize(camera, CameraManager()), request=request)


@router.post("/{camera_id}/restart", dependencies=[require_permission("camera:manage")])
async def restart_camera(camera_id: int, request: Request, db: DbSession, user: CurrentUser):
    return await start_camera(camera_id, request, db, user)


@router.post("/{camera_id}/stop", dependencies=[require_permission("camera:manage")])
async def stop_camera(camera_id: int, request: Request, db: DbSession, user: CurrentUser):
    from camera.camera_manager import CameraManager

    camera = await db.get(Camera, camera_id)
    if not camera:
        raise AppException(40420, "摄像头不存在")
    _assert_cam_access(user, camera)
    CameraManager().stop_camera(camera_id)
    camera.status = "OFFLINE"
    await AuditService.log(
        db,
        user_id=user.id,
        action="camera.stop",
        module="camera",
        target_type="camera",
        target_id=camera.id,
        detail={"store_id": camera.store_id},
        request=request,
    )
    return success(_serialize(camera, CameraManager()), request=request)


@router.post("/{camera_id}/stream-ticket", dependencies=[require_permission("camera:read")])
async def create_camera_stream_ticket(camera_id: int, request: Request, db: DbSession, user: CurrentUser):
    from app.core.security import create_stream_ticket

    camera = await db.get(Camera, camera_id)
    if not camera:
        raise AppException(40420, "摄像头不存在")
    _assert_cam_access(user, camera)
    ticket = create_stream_ticket(
        user.id, camera_id, store_id=camera.store_id, token_version=int(getattr(user, "token_version", 0) or 0)
    )
    return success({"ticket": ticket, "expires_in": 120}, request=request)


@router.get("/{camera_id}/stream")
async def camera_stream(
    camera_id: int,
    request: Request,
    db: DbSession,
    access_token: str | None = None,
    ticket: str | None = None,
):
    from camera.camera_manager import CameraManager
    from app.core.security import decode_token
    from jose import JWTError
    from sqlalchemy.orm import selectinload
    from app.models import User

    token = None
    auth = request.headers.get("authorization") or ""
    stream_hdr = (request.headers.get("x-stream-ticket") or "").strip()
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1]
    elif stream_hdr:
        token = stream_hdr
    elif ticket:
        # <img> 预览只能走 query；须为短时 stream ticket，勿传长期员工 JWT
        token = ticket
    elif access_token:
        raise AppException(40101, "禁止在 query 传递长期令牌，请使用 Authorization 或短时 stream ticket", status_code=401)
    if not token:
        raise AppException(40100, "未登录", status_code=401)
    try:
        payload = decode_token(token)
        typ = payload.get("typ")
        if typ not in ("staff", "stream"):
            raise AppException(40100, "请使用员工账号或流票据", status_code=401)
        if typ == "stream" and int(payload.get("cam") or 0) != int(camera_id):
            raise AppException(40301, "票据与摄像头不匹配", status_code=403)
        if typ == "staff" and ticket and not auth.startswith("Bearer ") and not stream_hdr:
            raise AppException(40101, "query 仅允许短时 stream ticket", status_code=401)
        user = (
            await db.execute(
                select(User).options(selectinload(User.role)).where(User.id == int(payload["sub"]), User.status == 1)
            )
        ).scalar_one_or_none()
        if not user:
            raise AppException(40102, "用户不存在", status_code=401)
        if int(payload.get("ver") or 0) != int(getattr(user, "token_version", 0) or 0):
            raise AppException(40103, "令牌已失效", status_code=401)
        perms_ok = user.role.code == "SUPER_ADMIN"
        if not perms_ok:
            from app.api.deps import get_user_permissions

            perms = await get_user_permissions(db, user.role_id)
            perms_ok = "camera:read" in perms
        if not perms_ok:
            raise AppException(40300, "缺少权限: camera:read", status_code=403)
    except AppException:
        raise
    except (JWTError, KeyError, ValueError):
        raise AppException(40101, "无效令牌", status_code=401)

    camera = await db.get(Camera, camera_id)
    if not camera:
        raise AppException(40453, "摄像头不存在", status_code=404)

    # P2-4 票据门店与摄像头门店二次校验（即使 _assert_cam_access 被绕过也无法跨店）
    if typ == "stream":
        tk_store = payload.get("store")
        if tk_store is not None and int(tk_store) != int(camera.store_id or 0):
            raise AppException(40301, "票据门店与摄像头不匹配", status_code=403)

    try:
        _assert_cam_access(user, camera)
    except AppException:
        raise AppException(40301, "无权查看该门店摄像头", status_code=403)

    mgr = CameraManager()
    worker = mgr.get_worker(camera_id)
    if not worker:
        _start_worker(camera)
        worker = mgr.get_worker(camera_id)

    def generate():
        while True:
            if worker:
                frame, _ = worker.frame_buffer.get_frame()
                if frame is not None:
                    _, jpeg = cv2.imencode(".jpg", frame)
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
            time.sleep(max(0.04, 1.0 / max((worker.fps if worker else 25), 1)))

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")

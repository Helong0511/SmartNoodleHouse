"""摄像头 API：鉴权 + 解密密码连接 + 生命周期 + 访客统计"""
from __future__ import annotations

import asyncio
import logging
import time

import cv2
from fastapi import APIRouter, Request, Response
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

def _is_valid_visitor_snapshot_bytes(data: bytes) -> bool:
    """仅接受有 JPEG SOI 魔数且大小受限的访客抓拍。"""
    return bool(data) and len(data) <= 10 * 1024 * 1024 and data.startswith(b"\xff\xd8\xff")


logger = logging.getLogger(__name__)


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
    from camera.camera_manager import CameraManager, CameraNotLeaderError

    source = build_connect_source(camera.source_url, camera.username, camera.password_encrypted)
    try:
        CameraManager().start_camera(camera.id, source, camera.fps or 25, force=force)
    except CameraNotLeaderError as exc:
        raise AppException(
            40922,
            "当前实例非 Leader，无法启采",
            status_code=409,
        ) from exc


async def _bump_speak_gen_safe(camera_id: int, *, reason: str) -> None:
    """force 启采/停采后打断进行中对讲；失败仅记日志。"""
    try:
        from app.services.face_service import WelcomeService

        await WelcomeService.bump_speak_generation_async(camera_id)
    except Exception:
        logger.debug(
            "%s bump speak_gen failed camera_id=%s", reason, camera_id, exc_info=True
        )


def _require_leader_for_capture_control(*, action: str) -> None:
    """停采/删机须在 Leader：非 Leader 本地 stop 假成功会导致库 OFFLINE 而采集仍跑。"""
    from app.core.leader_lock import is_leader_held

    if not is_leader_held():
        raise AppException(
            40922,
            f"当前实例非 Leader，无法{action}",
            status_code=409,
        )


def _serialize(c: Camera, mgr) -> dict:
    runtime = mgr.get_runtime(c.id) if mgr else {"running": False, "status": c.status, "has_frame": False}
    try:
        from app.services.face_pipeline_metrics import get_recognition_metrics

        metrics = get_recognition_metrics(c.id)
        if metrics:
            runtime = {
                **runtime,
                "recognition": {
                    k: metrics[k]
                    for k in (
                        "detect_extract_ms",
                        "refine_extract_ms",
                        "match_ms",
                        "frame_age_ms",
                        "face_ok",
                        "face_area_ratio",
                        "face_side_px",
                        "frame_w",
                        "frame_h",
                        "light_detect",
                        "det_score",
                        "best_similarity",
                        "matched_customer_id",
                        "threshold",
                        "at",
                    )
                    if k in metrics
                },
            }
    except Exception:
        pass
    # 与 overview 一致：有采集线程用 runtime；Leader 无 worker 勿用库内 ONLINE/DEGRADED 覆盖
    if runtime.get("running"):
        status_out = runtime.get("status") or "OFFLINE"
    else:
        try:
            from app.core.leader_lock import is_leader_held

            on_leader = bool(is_leader_held())
        except Exception:
            on_leader = False
        db_st = str(c.status or "").upper()
        if on_leader and db_st in ("ONLINE", "DEGRADED", "RECONNECTING"):
            status_out = "OFFLINE"
        else:
            status_out = c.status or "OFFLINE"
    return {
        "id": c.id,
        "name": c.name,
        "camera_type": c.camera_type,
        "source_url": mask_rtsp_url(c.source_url),
        "username": c.username,
        "status": status_out,
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
    degraded = 0
    reconnecting = 0
    try:
        from app.core.leader_lock import is_leader_held

        on_leader = bool(is_leader_held())
    except Exception:
        on_leader = False
    for c in cams:
        # 与 _serialize 一致：以线程存活为准，勿仅凭 worker 对象残留计在线
        rt = mgr.get_runtime(c.id)
        if rt.get("running"):
            st = rt.get("status") or "OFFLINE"
        elif on_leader:
            # Leader 无线程：勿把库内残留 ONLINE/DEGRADED 计入在线（非 Leader 仍信库）
            db_st = str(c.status or "").upper()
            st = (
                "OFFLINE"
                if db_st in ("ONLINE", "DEGRADED", "RECONNECTING")
                else (db_st or "OFFLINE")
            )
        else:
            st = c.status or "OFFLINE"
        # DEGRADED：偶发坏帧但仍有近期好帧，识别 Worker 仍跑；KPI「在线」须计入
        if st == "ONLINE":
            online += 1
        elif st == "DEGRADED":
            degraded += 1
        elif st == "RECONNECTING":
            reconnecting += 1

    usable = online + degraded
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
            "online": usable,
            "degraded": degraded,
            "reconnecting": reconnecting,
            "offline": max(0, len(cams) - usable - reconnecting),
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
    # 到期访客生物识别记录即使清理任务延迟，也不得继续展示或签发图片 URL。
    q = q.where(VisitorEvent.expires_at > now_local())
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
    from app.models import Customer
    from app.services.customer_service import CustomerService
    from app.services.face_service import VisitorService
    from app.services.visitor_snapshot_access import build_visitor_snapshot_url

    camera_ids = {int(e.camera_id) for e in rows if getattr(e, "camera_id", None)}
    camera_store_map: dict[int, int] = {}
    if camera_ids:
        camera_store_map = {
            int(cid): int(sid)
            for cid, sid in (
                await db.execute(
                    select(Camera.id, Camera.store_id).where(Camera.id.in_(list(camera_ids)))
                )
            ).all()
            if sid is not None
        }

    cust_ids = {int(e.customer_id) for e in rows if getattr(e, "customer_id", None)}
    cust_map: dict[int, Customer] = {}
    if cust_ids:
        cust_map = {
            int(c.id): c
            for c in (
                await db.execute(select(Customer).where(Customer.id.in_(list(cust_ids))))
            ).scalars().all()
        }
    items = []
    for e in rows:
        cid = int(e.customer_id) if getattr(e, "customer_id", None) else None
        cust = cust_map.get(cid) if cid else None
        snapshot_key = VisitorService.uploads_url_to_key(e.snapshot_path)
        store_for_snapshot = camera_store_map.get(int(e.camera_id)) if e.camera_id else None
        snapshot_url = (
            build_visitor_snapshot_url(int(e.id), int(store_for_snapshot), snapshot_key)
            if snapshot_key and store_for_snapshot is not None
            else None
        )
        item = {
            "id": e.id,
            "visitor_no": e.visitor_no,
            "camera_id": e.camera_id,
            "customer_id": cid,
            "customer_no": cust.customer_no if cust else None,
            "event_type": e.event_type,
            "event_time": serialize_dt(e.event_time),
            "expires_at": serialize_dt(e.expires_at),
            # 不再暴露可直接公开访问的 /uploads/visitors 路径，改为 5 分钟签名 URL。
            "snapshot_url": snapshot_url,
            "is_member": bool(cid),
        }
        item.update(CustomerService.staff_customer_name_fields(cust))
        items.append(item)
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


@router.get("/visitors/snapshot/{event_id}")
async def get_visitor_snapshot(
    event_id: int,
    exp: int,
    sig: str,
    db: DbSession,
):
    """短时 HMAC 签名访问访客抓拍；静态 /uploads/visitors 路径不再公开。"""
    from app.services.face_service import VisitorService
    from app.services.object_storage import get_storage
    from app.services.visitor_snapshot_access import verify_visitor_snapshot_signature

    event = await db.get(VisitorEvent, int(event_id))
    if (
        not event
        or not event.snapshot_path
        or (event.expires_at is not None and event.expires_at <= now_local())
    ):
        raise AppException(40425, "访客抓拍不存在或访问链接已过期", status_code=404)
    camera = await db.get(Camera, int(event.camera_id))
    if not camera or camera.store_id is None:
        raise AppException(40425, "访客抓拍不存在或访问链接已过期", status_code=404)
    object_key = VisitorService.uploads_url_to_key(event.snapshot_path)
    if not object_key or not verify_visitor_snapshot_signature(
        int(event.id), int(camera.store_id), object_key, exp, sig
    ):
        raise AppException(40425, "访客抓拍不存在或访问链接已过期", status_code=404)
    try:
        data = await get_storage().get_bytes(object_key)
    except FileNotFoundError:
        raise AppException(40425, "访客抓拍文件不存在", status_code=404) from None
    except Exception as exc:
        logger.warning("读取访客抓拍失败 event_id=%s: %s", event_id, exc)
        raise AppException(50325, "访客抓拍暂时无法读取", status_code=503) from exc
    if not _is_valid_visitor_snapshot_bytes(data):
        raise AppException(40425, "访客抓拍文件无效", status_code=404)
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="visitor.jpg"',
        },
    )


class TestSpeakBody(BaseModel):
    text: str = Field(default="欢迎光临", min_length=1, max_length=80)
    # pc=仅电脑扬声器；camera=仅摄像头对讲；both=先摄像头失败再本机（兼容旧试听）
    target: str = Field(default="pc", pattern="^(pc|camera|both)$")


@router.post("/{camera_id}/probe-face", dependencies=[require_permission("camera:manage")])
async def probe_camera_face(
    camera_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """对当前实时帧做人脸探测+匹配（不落库、不播报），用于排查「识别到却不迎宾」。"""
    import time as _time

    import numpy as np
    from camera.camera_manager import CameraManager

    from app.core.config import get_settings
    from app.services.face_extractor import extract_live_face_hit
    from app.services.face_service import face_embedding_cache
    from app.services.system_settings_service import SystemSettingsService

    camera = await db.get(Camera, camera_id)
    if not camera:
        raise AppException(40420, "摄像头不存在")
    _assert_cam_access(user, camera)

    mgr = CameraManager()
    worker = mgr.get_worker(camera_id)
    if not worker:
        raise AppException(40921, "摄像头未启动", status_code=409)
    frame, ts = worker.frame_buffer.get_frame()
    age = worker.frame_buffer.age_sec()
    if frame is None:
        return success(
            {
                "ok": False,
                "reason": "no_frame",
                "status": getattr(worker, "status", None),
                "age": age,
            },
            request=request,
        )
    # 与识别 Worker 一致：过期帧不做匹配，避免用墙钟跳变或断流残留误诊
    max_frame_age_sec = 5.0
    if age is None or float(age) > max_frame_age_sec:
        return success(
            {
                "ok": False,
                "reason": "stale_frame",
                "status": getattr(worker, "status", None),
                "age": age,
                "max_age_sec": max_frame_age_sec,
                "hint": "帧过期或无有效时间戳，请确认摄像头 ONLINE/DEGRADED 且有新帧后再探测",
            },
            request=request,
        )

    settings = get_settings()
    store_id = int(camera.store_id) if camera.store_id else None
    threshold = await SystemSettingsService.effective_float(
        db, "face_match_threshold", settings.face_match_threshold, store_id=store_id
    )
    hit = None
    t0 = _time.perf_counter()
    try:
        hit = await asyncio.to_thread(extract_live_face_hit, frame, light=False)
        emb = np.asarray(hit.embedding, dtype=np.float32)
        face_ok = True
        face_err = None
    except Exception as e:
        emb = None
        face_ok = False
        face_err = str(e)[:120]
    detect_ms = int((_time.perf_counter() - t0) * 1000)

    best_id, best_sim = None, 0.0
    match_ms = None
    if face_ok and emb is not None:
        t1 = _time.perf_counter()
        best_id, best_sim = await face_embedding_cache.best_match(
            db, emb, float(threshold), camera_id=camera_id
        )
        match_ms = int((_time.perf_counter() - t1) * 1000)

    shape = list(getattr(frame, "shape", []) or [])
    return success(
        {
            "ok": face_ok,
            "reason": None if face_ok else "no_face",
            "error": face_err,
            "status": getattr(worker, "status", None),
            "age": age,
            "shape": shape,
            "threshold": threshold,
            "detect_extract_ms": detect_ms,
            "match_ms": match_ms,
            "face_area_ratio": (
                round(float(hit.face_area_ratio), 4) if hit is not None else None
            ),
            "face_side_px": int(hit.face_side_px) if hit is not None else None,
            "det_score": (
                round(float(hit.det_score), 3) if hit is not None else None
            ),
            "sharpness": (
                round(float(hit.sharpness), 1) if hit is not None else None
            ),
            "best_customer_id": best_id,
            "best_similarity": float(best_sim) if best_sim is not None else None,
            "matched": bool(best_id is not None and float(best_sim) >= float(threshold)),
            "hint": (
                None
                if not face_ok
                else (
                    "脸框短边偏小，远景/子码流可能不够认；请走近或提高分辨率后再测"
                    if hit is not None and int(hit.face_side_px or 0) > 0 and int(hit.face_side_px) < 80
                    else None
                )
            ),
        },
        request=request,
    )


@router.post("/{camera_id}/test-speak", dependencies=[require_permission("camera:manage")])
async def test_camera_speak(
    camera_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    body: TestSpeakBody | None = None,
):
    """立即入队试听播报：可分别测电脑扬声器或摄像头对讲。"""
    from app.services.face_service import WelcomeService

    camera = await db.get(Camera, camera_id)
    if not camera:
        raise AppException(40420, "摄像头不存在")
    _assert_cam_access(user, camera)
    text = ((body.text if body else None) or "欢迎光临").strip()
    target = ((body.target if body else None) or "pc").strip().lower()
    if target not in ("pc", "camera", "both"):
        target = "pc"
    kind = {
        "pc": "test_speak_pc",
        "camera": "test_speak_camera",
        "both": "test_speak",
    }[target]
    task_id = await WelcomeService.enqueue(
        {
            "customer_id": None,
            "text": text,
            "camera_id": int(camera_id),
            "kind": kind,
            "target": target,
        }
    )
    queued = bool(task_id)
    await AuditService.log(
        db,
        user_id=user.id,
        action="camera.test_speak",
        module="camera",
        target_type="camera",
        target_id=camera_id,
        detail={"text": text, "target": target, "task_id": task_id, "queued": queued},
        request=request,
        store_id=int(camera.store_id) if camera.store_id else None,
    )
    await db.commit()
    return success(
        {
            "queued": queued,
            "camera_id": camera_id,
            "text": text,
            "target": target,
            "task_id": task_id,
            "note": (
                "queued 仅表示入队；查 GET /cameras/speak-tasks/{task_id}；设备出声需现场听声确认"
                if queued
                else "入队失败（队列满或被跳过）；未产生可轮询 task_id"
            ),
        },
        request=request,
    )


@router.get("/speak-tasks/{task_id}", dependencies=[require_permission("camera:read")])
async def get_speak_task_status(
    task_id: str,
    request: Request,
    user: CurrentUser,
):
    """查询迎宾/试听播报任务状态（非设备扬声器 ACK）。"""
    from app.services.face_service import WelcomeService

    data = await WelcomeService.get_speak_task(task_id)
    if not data:
        raise AppException(40421, "播报任务不存在或已过期")
    return success(data, request=request)


@router.delete("/visitors/events", dependencies=[require_permission("camera:manage")])
async def clear_visitor_events(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    camera_id: int | None = None,
    store_id: int | None = None,
):
    """清空最近访客事件（含抓拍图）。可按摄像头或门店范围；无参时清本店（超管须指定门店或摄像头）。"""
    from app.services.face_service import VisitorService

    cam_ids: list[int] = []
    if camera_id:
        camera = await db.get(Camera, camera_id)
        if not camera:
            raise AppException(40420, "摄像头不存在")
        _assert_cam_access(user, camera)
        cam_ids = [int(camera.id)]
        scope_store = int(camera.store_id) if camera.store_id else None
    else:
        sid = require_bound_store(user, store_id)
        if sid is None:
            raise AppException(40020, "请指定门店或摄像头后再清空访客事件")
        assert_store_access(user, sid)
        cam_ids = list(
            (await db.execute(select(Camera.id).where(Camera.store_id == int(sid)))).scalars().all()
        )
        scope_store = int(sid)
    stats = await VisitorService.clear_events(db, camera_ids=cam_ids)
    await AuditService.log(
        db,
        user_id=user.id,
        action="camera.visitors.clear",
        module="camera",
        target_type="store" if scope_store else "camera",
        target_id=scope_store or (cam_ids[0] if cam_ids else None),
        detail={"camera_ids": cam_ids, **stats},
        request=request,
        store_id=scope_store,
    )
    await db.commit()
    return success(stats, request=request)


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
    # 新建网络摄像头：名称写入设备右下角通道名 OSD（失败不影响创建）
    osd_sync: dict | None = None
    if (camera.camera_type or "").upper() == "RTSP" and (camera.name or "").strip():
        from app.services.hikvision_audio import try_sync_camera_osd_name

        osd_sync = await try_sync_camera_osd_name(
            source_url=camera.source_url,
            username=camera.username,
            password_encrypted=camera.password_encrypted,
            display_name=camera.name,
        )
    if data.auto_start:
        from camera.camera_manager import CameraManager

        start_note: str | None = None
        try:
            # 与 start/update 对称：先 bump 再 force 启采；启采 join 放线程池
            await _bump_speak_gen_safe(camera.id, reason="create")
            await asyncio.to_thread(lambda: _start_worker(camera, force=True))
            await asyncio.sleep(0.3)
            mgr = CameraManager()
            st = mgr.get_status(camera.id)
            camera.status = st if st else "OFFLINE"
            if camera.status in ("ONLINE", "DEGRADED"):
                camera.last_online_at = now_local()
        except AppException as exc:
            # 非 Leader 不得整单回滚创建；机位保留 OFFLINE，前端可再启采
            if getattr(exc, "code", None) != 40922:
                raise
            camera.status = "OFFLINE"
            start_note = "not_leader"
            mgr = CameraManager()
        payload = _serialize(camera, mgr)
        if osd_sync is not None:
            payload["osd_sync"] = osd_sync
        if start_note:
            payload["start_note"] = start_note
        return success(payload, request=request)
    from camera.camera_manager import CameraManager

    payload = _serialize(camera, CameraManager())
    if osd_sync is not None:
        payload["osd_sync"] = osd_sync
    return success(payload, request=request)


@router.put("/{camera_id}", dependencies=[require_permission("camera:manage")])
async def update_camera(camera_id: int, data: CameraUpdate, request: Request, db: DbSession, user: CurrentUser):
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise AppException(40420, "摄像头不存在")
    _assert_cam_access(user, camera)
    payload = data.model_dump(exclude_unset=True)
    restart = False
    name_before = (camera.name or "").strip()
    name_changed = False
    if "name" in payload and payload["name"]:
        new_name = str(payload["name"]).strip()
        if new_name:
            name_changed = new_name != name_before
            camera.name = new_name
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
        elif payload.get("clear_password"):
            pass
        elif "source_url" in payload and plain_pwd is None and not payload.get("password") and not payload.get("clear_password"):
            # 仅改 URL 时若 URL 内嵌了密码，prepare 已拆出 plain_pwd；否则保留原加密口令
            pass
        restart = True
        # 改地址/账号/密码后必须丢掉旧 Digest Client 与 codec 缓存，否则会 401 到进程重启
        try:
            from app.services.hikvision_audio import (
                invalidate_hikvision_caches,
                resolve_isapi_base,
            )

            isapi = resolve_isapi_base(camera.source_url or "")
            if isapi:
                await invalidate_hikvision_caches(base_url=isapi, channel=1)
        except Exception:
            pass
    if "fps" in payload and payload["fps"] is not None:
        camera.fps = payload["fps"]
        restart = True
    if "detect_fps" in payload and payload["detect_fps"] is not None:
        camera.detect_fps = payload["detect_fps"]
    await db.flush()
    from camera.camera_manager import CameraManager

    mgr = CameraManager()
    restart_note: str | None = None
    if restart:
        # 勿仅信本地 get_worker：请求可能落在非 Leader，Leader 上仍跑旧 RTSP
        # 先 bump 再 force 启采：避免新线程已起、gen0 未采时 bump 被吸收、旧句推完
        try:
            await _bump_speak_gen_safe(camera_id, reason="update")
            await asyncio.to_thread(lambda: _start_worker(camera, force=True))
            camera.status = mgr.get_status(camera_id) or "OFFLINE"
            if camera.status in ("ONLINE", "DEGRADED"):
                camera.last_online_at = now_local()
        except AppException as exc:
            if getattr(exc, "code", None) != 40922:
                raise
            restart_note = "not_leader"
    # 仅名称实际变更时同步海康右下角通道名 OSD（改地址/密码不同步）
    osd_sync: dict | None = None
    if name_changed and (camera.camera_type or "").upper() == "RTSP":
        from app.services.hikvision_audio import try_sync_camera_osd_name

        osd_sync = await try_sync_camera_osd_name(
            source_url=camera.source_url,
            username=camera.username,
            password_encrypted=camera.password_encrypted,
            display_name=camera.name,
        )
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
            "restart_note": restart_note,
            "osd_sync": osd_sync,
        },
        request=request,
    )
    body = _serialize(camera, mgr)
    if osd_sync is not None:
        body["osd_sync"] = osd_sync
    if restart_note:
        body["restart_note"] = restart_note
    return success(body, request=request)


@router.delete("/{camera_id}", dependencies=[require_permission("camera:manage")])
async def delete_camera(camera_id: int, request: Request, db: DbSession, user: CurrentUser):
    from camera.camera_manager import CameraManager

    camera = await db.get(Camera, camera_id)
    if not camera:
        raise AppException(40420, "摄像头不存在")
    _assert_cam_access(user, camera)
    _require_leader_for_capture_control(action="删除摄像头")
    store_id = camera.store_id
    name = camera.name
    # 先 bump 再 stop（对齐 start）：join 前即可打断对讲；stop 放线程池免堵事件循环
    await _bump_speak_gen_safe(camera_id, reason="delete")
    await asyncio.to_thread(CameraManager().stop_camera, camera_id)
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
    # 先 bump 再 force 启采：否则新线程已存在时 camera_stopped 放过，
    # 且 bump 落在 gen0 之前会被吸收，旧句仍推完
    await _bump_speak_gen_safe(camera_id, reason="start")
    await asyncio.to_thread(lambda: _start_worker(camera, force=True))
    await asyncio.sleep(0.3)
    from camera.camera_manager import CameraManager

    st = CameraManager().get_status(camera_id) or "OFFLINE"
    camera.status = st
    if st in ("ONLINE", "DEGRADED"):
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
    # 非 Leader 不得写库 OFFLINE：Leader 上采集仍会跑，造成假停采
    _require_leader_for_capture_control(action="停采")
    # 先 bump 再 stop（对齐 start）：join 前即可打断对讲；stop 放线程池免堵事件循环
    await _bump_speak_gen_safe(camera_id, reason="stop")
    await asyncio.to_thread(CameraManager().stop_camera, camera_id)
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
        await asyncio.to_thread(lambda: _start_worker(camera))
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

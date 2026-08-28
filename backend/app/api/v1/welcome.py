from datetime import datetime

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, require_permission
from app.core.response import calc_pages, now_local, success
from app.services.face_service import FaceService
from app.services.store_scope import require_bound_store

router = APIRouter(prefix="/welcome", tags=["欢迎"])


@router.post("/face/revoke")
async def revoke_face(request: Request, db: DbSession, customer: CurrentCustomer):
    await FaceService.revoke_face(db, customer.id)
    return success({"revoked": True}, request=request)


@router.get("/stats", dependencies=[require_permission("welcome:manage")])
async def welcome_stats(
    request: Request, db: DbSession, user: CurrentUser, store_id: int | None = None
):
    from app.models import Camera, WelcomeLog

    today = now_local().replace(hour=0, minute=0, second=0, microsecond=0)
    q = select(func.count()).select_from(WelcomeLog)
    q_today = select(func.count()).select_from(WelcomeLog).where(WelcomeLog.created_at >= today)
    # 非超管强制本店（未绑店 403）；超管可传 store_id 或看全店
    effective_store = require_bound_store(user, store_id)
    cam_ids = None
    if effective_store is not None:
        cam_ids = list(
            (await db.execute(select(Camera.id).where(Camera.store_id == effective_store))).scalars().all()
        )
        # 含无摄像头关联的门店外日志不展示；本店仅看本店摄像头
        q = q.where(WelcomeLog.camera_id.in_(cam_ids or [-1]))
        q_today = q_today.where(WelcomeLog.camera_id.in_(cam_ids or [-1]))
    total = int(await db.scalar(q) or 0)
    today_cnt = int(await db.scalar(q_today) or 0)
    unique_q = select(func.count(func.distinct(WelcomeLog.customer_id))).select_from(WelcomeLog)
    if effective_store is not None:
        unique_q = unique_q.where(WelcomeLog.camera_id.in_(cam_ids or [-1]))
    unique_customers = int(await db.scalar(unique_q) or 0)
    return success(
        {"total": total, "today": today_cnt, "unique_customers": unique_customers},
        request=request,
    )


@router.get("/logs", dependencies=[require_permission("welcome:manage")])
async def welcome_logs(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    customer_id: int | None = None,
    camera_id: int | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    store_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
    limit: int | None = None,
):
    from app.models import Camera, Customer, WelcomeLog

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = select(WelcomeLog)
    effective_store = require_bound_store(user, store_id)
    if effective_store is not None:
        cam_ids = list(
            (await db.execute(select(Camera.id).where(Camera.store_id == effective_store))).scalars().all()
        )
        q = q.where(WelcomeLog.camera_id.in_(cam_ids or [-1]))
    if customer_id:
        q = q.where(WelcomeLog.customer_id == customer_id)
    if camera_id:
        q = q.where(WelcomeLog.camera_id == camera_id)
    if keyword:
        kw = f"%{keyword.strip()}%"
        q = q.where(WelcomeLog.welcome_text.like(kw))
    if date_from:
        try:
            q = q.where(WelcomeLog.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            end = datetime.fromisoformat(date_to)
            if len(date_to) <= 10:
                from datetime import timedelta

                end = end + timedelta(days=1)
            q = q.where(WelcomeLog.created_at < end)
        except ValueError:
            pass

    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    q = q.order_by(WelcomeLog.created_at.desc())
    if limit is not None:
        rows = list((await db.execute(q.limit(min(limit, 200)))).scalars().all())
    else:
        rows = list((await db.execute(q.offset((page - 1) * page_size).limit(page_size))).scalars().all())

    cust_ids = list({w.customer_id for w in rows})
    cam_ids2 = list({w.camera_id for w in rows if w.camera_id})
    cust_map = {}
    if cust_ids:
        cust_map = {
            c.id: c
            for c in (await db.execute(select(Customer).where(Customer.id.in_(cust_ids)))).scalars().all()
        }
    cam_map = {}
    if cam_ids2:
        cam_map = {
            c.id: c for c in (await db.execute(select(Camera).where(Camera.id.in_(cam_ids2)))).scalars().all()
        }

    items = []
    for w in rows:
        c = cust_map.get(w.customer_id)
        cam = cam_map.get(w.camera_id) if w.camera_id else None
        items.append(
            {
                "id": w.id,
                "customer_id": w.customer_id,
                "customer_name": (c.display_name or c.nickname) if c else None,
                "customer_no": c.customer_no if c else None,
                "camera_id": w.camera_id,
                "camera_name": cam.name if cam else None,
                "text": w.welcome_text,
                "created_at": w.created_at.isoformat() if w.created_at else None,
            }
        )
    if limit is not None:
        return success(items, request=request)
    return success(
        {
            "items": items,
            "total": total or 0,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total or 0, page_size),
        },
        request=request,
    )

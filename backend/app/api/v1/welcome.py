from fastapi import APIRouter, Request

from app.api.deps import CurrentCustomer, DbSession, require_permission
from app.core.response import success
from app.services.face_service import FaceService

router = APIRouter(prefix="/welcome", tags=["欢迎"])


@router.post("/face/revoke")
async def revoke_face(request: Request, db: DbSession, customer: CurrentCustomer):
    await FaceService.revoke_face(db, customer.id)
    return success({"revoked": True}, request=request)


@router.get("/logs")
async def welcome_logs(
    request: Request,
    db: DbSession,
    user=require_permission("welcome:manage"),
    customer_id: int | None = None,
    limit: int = 50,
):
    from app.models import WelcomeLog
    from sqlalchemy import select

    q = select(WelcomeLog).order_by(WelcomeLog.created_at.desc()).limit(limit)
    if customer_id:
        q = q.where(WelcomeLog.customer_id == customer_id)
    result = await db.execute(q)
    return success(
        [{"customer_id": w.customer_id, "text": w.welcome_text, "created_at": w.created_at.isoformat()} for w in result.scalars()],
        request=request,
    )

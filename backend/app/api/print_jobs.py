"""打印任务：管理端列表与手动重试。"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, require_kitchen
from app.core.exceptions import AppException
from app.core.response import calc_pages, success, serialize_dt
from app.models import PrintJob
from app.services.audit_service import AuditService
from app.services.print_service import PrintService
from app.services.store_scope import assert_store_access, require_bound_store

router = APIRouter(prefix="/print-jobs", tags=["打印任务"])


@router.get("", dependencies=[require_kitchen()])
async def list_print_jobs(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="FAILED/RETRYING/SUCCESS/PRINTING/PENDING，逗号分隔"),
    store_id: int | None = None,
    order_id: int | None = None,
):
    sid = require_bound_store(user, store_id)
    q = select(PrintJob)
    cq = select(func.count()).select_from(PrintJob)
    if sid is not None:
        q = q.where(PrintJob.store_id == sid)
        cq = cq.where(PrintJob.store_id == sid)
    if order_id:
        q = q.where(PrintJob.order_id == int(order_id))
        cq = cq.where(PrintJob.order_id == int(order_id))
    if status:
        statuses = [s.strip().upper() for s in status.split(",") if s.strip()]
        if statuses:
            q = q.where(PrintJob.status.in_(statuses))
            cq = cq.where(PrintJob.status.in_(statuses))
    total = int((await db.execute(cq)).scalar() or 0)
    rows = (
        await db.execute(
            q.order_by(PrintJob.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    items = [
        {
            "id": j.id,
            "store_id": j.store_id,
            "order_id": j.order_id,
            "ticket_type": j.ticket_type,
            "status": j.status,
            "attempt_count": j.attempt_count,
            "next_retry_at": serialize_dt(j.next_retry_at),
            "last_error": j.last_error,
            "succeeded_at": serialize_dt(j.succeeded_at),
            "created_at": serialize_dt(j.created_at),
            "updated_at": serialize_dt(j.updated_at) if getattr(j, "updated_at", None) else None,
        }
        for j in rows
    ]
    return success(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
        },
        request=request,
    )


@router.post("/{job_id}/retry", dependencies=[require_kitchen()])
async def retry_print_job(job_id: int, request: Request, db: DbSession, user: CurrentUser):
    job = await db.get(PrintJob, int(job_id))
    if not job:
        raise AppException(40404, "打印任务不存在")
    assert_store_access(user, job.store_id)
    if job.status not in ("FAILED", "RETRYING"):
        raise AppException(40031, f"当前状态 {job.status} 不可手动重试")
    # 允许立刻重试：清 next_retry_at
    job.next_retry_at = None
    if job.status == "FAILED":
        job.status = "RETRYING"
    await db.commit()
    ok = await PrintService.process_print_job(int(job_id))
    refreshed = await db.get(PrintJob, int(job_id))
    await AuditService.log_staff(
        db,
        user=user,
        action="print_job.retry",
        module="print_job",
        target_type="print_job",
        target_id=int(job_id),
        detail={
            "ok": bool(ok),
            "status": refreshed.status if refreshed else None,
            "store_id": int(job.store_id) if getattr(job, "store_id", None) else None,
        },
        request=request,
        store_id=int(job.store_id) if getattr(job, "store_id", None) else None,
    )
    return success(
        {
            "retried": True,
            "ok": bool(ok),
            "id": job_id,
            "status": refreshed.status if refreshed else None,
            "last_error": refreshed.last_error if refreshed else None,
        },
        request=request,
    )

"""打印任务：管理端列表与手动重试。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.api.deps import CurrentUser, DbSession, require_kitchen
from app.core.exceptions import AppException
from app.core.orm_safe import orm_attr_loaded
from app.core.response import calc_pages, success, serialize_dt, now_local
from app.models import PrintJob
from app.services.audit_service import AuditService
from app.services.print_service import PrintService
from app.services.store_scope import assert_store_access, require_bound_store

router = APIRouter(prefix="/print-jobs", tags=["打印任务"])


class ResolveUncertainPrintJobIn(BaseModel):
    outcome: Literal["PRINTED", "NOT_PRINTED"]
    reason: str = Field(..., min_length=8, max_length=500)


@router.get("", dependencies=[require_kitchen()])
async def list_print_jobs(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="FAILED/RETRYING/SUCCESS/PRINTING/PENDING/UNCERTAIN，逗号分隔"),
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
            "locked_at": serialize_dt(getattr(j, "locked_at", None)),
            "last_error": j.last_error,
            "succeeded_at": serialize_dt(j.succeeded_at),
            "created_at": serialize_dt(j.created_at) if orm_attr_loaded(j, "created_at") else None,
            "updated_at": serialize_dt(j.updated_at) if orm_attr_loaded(j, "updated_at") else None,
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

@router.post("/{job_id}/resolve-uncertain", dependencies=[require_kitchen()])
async def resolve_uncertain_print_job(
    job_id: int,
    data: ResolveUncertainPrintJobIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """操作员核对纸面输出后处置结果未知任务；仅当前 UNCERTAIN 状态可转换。"""
    job = await db.get(PrintJob, int(job_id))
    if not job:
        raise AppException(40404, "打印任务不存在")
    assert_store_access(user, job.store_id)
    reason = data.reason.strip()
    if len(reason) < 8:
        raise AppException(40012, "核验原因去除空格后至少需要 8 个字符")
    if job.status != "UNCERTAIN":
        raise AppException(40911, f"当前状态 {job.status} 不是待核验状态", status_code=409)

    now = now_local()
    if data.outcome == "PRINTED":
        new_status = "SUCCESS"
        values = {
            "status": new_status,
            "succeeded_at": now,
            "last_error": None,
            "next_retry_at": None,
            "locked_at": None,
            "locked_by": None,
        }
    else:
        # 操作员已核实未出纸；显式授权恢复自动队列，不在请求内直接占用打印机。
        new_status = "RETRYING"
        values = {
            "status": new_status,
            "succeeded_at": None,
            "last_error": "MANUALLY_CONFIRMED_NOT_PRINTED",
            "next_retry_at": now,
            "locked_at": None,
            "locked_by": None,
        }

    changed = await db.execute(
        update(PrintJob)
        .where(PrintJob.id == int(job_id), PrintJob.status == "UNCERTAIN")
        .values(**values)
    )
    if not changed.rowcount:
        await db.rollback()
        raise AppException(40912, "打印任务状态已变化，请刷新后重新核验", status_code=409)
    await db.commit()
    await AuditService.log_staff(
        db,
        user=user,
        action="print_job.resolve_uncertain",
        module="print_job",
        target_type="print_job",
        target_id=int(job_id),
        detail={
            "outcome": data.outcome,
            "status": new_status,
            "reason": reason,
            "store_id": int(job.store_id) if getattr(job, "store_id", None) else None,
        },
        request=request,
        store_id=int(job.store_id) if getattr(job, "store_id", None) else None,
    )
    await db.commit()
    return success(
        {
            "id": int(job_id),
            "outcome": data.outcome,
            "status": new_status,
            "retry_queued": data.outcome == "NOT_PRINTED",
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

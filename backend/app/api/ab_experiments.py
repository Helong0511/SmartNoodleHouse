"""管理端 A/B 实验 CRUD 与效果报表。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Request

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import success, serialize_dt
from app.schemas.reco import AbExperimentUpsert
from app.services.ab_service import AbService
from app.services.audit_service import AuditService

router = APIRouter(prefix="/admin/ab-experiments", tags=["A/B实验"])


def _experiment_dict(exp) -> dict:
    return {
        "key": exp.experiment_key,
        "name": exp.name,
        "status": exp.status,
        "traffic_percent": exp.traffic_percent,
        "default_bucket": exp.default_bucket,
        "buckets": exp.buckets or [],
        "description": exp.description,
        "updated_at": serialize_dt(exp.updated_at) if exp.updated_at else None,
    }


def _parse_date(value: str | None, *, end: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.strptime(value.strip()[:10], "%Y-%m-%d")
    except ValueError as e:
        raise AppException(40001, "日期格式应为 YYYY-MM-DD") from e
    return d + timedelta(days=1) if end else d


@router.get("", dependencies=[require_permission("order:read")])
async def list_experiments(request: Request, db: DbSession):
    rows = await AbService.list_experiments(db)
    return success([_experiment_dict(e) for e in rows], request=request)


@router.get("/{key}", dependencies=[require_permission("order:read")])
async def get_experiment(key: str, request: Request, db: DbSession):
    exp = await AbService.get_experiment(db, key)
    if not exp:
        raise AppException(40404, "实验不存在")
    return success(_experiment_dict(exp), request=request)


@router.put("/{key}", dependencies=[require_permission("system:manage")])
async def upsert_experiment(
    key: str,
    data: AbExperimentUpsert,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    payload = data.model_dump()
    try:
        exp = await AbService.upsert_experiment(db, key, payload)
    except ValueError as e:
        raise AppException(40001, str(e)) from e
    await AuditService.log_staff(
        db,
        user=user,
        action="ab.experiment.upsert",
        module="system",
        target_type="ab_experiment",
        target_id=None,  # key 为字符串，不能写入 bigint target_id
        detail={"key": key, "status": exp.status, "traffic_percent": exp.traffic_percent},
        request=request,
    )
    return success(_experiment_dict(exp), request=request)


@router.delete("/{key}", dependencies=[require_permission("system:manage")])
async def delete_experiment(
    key: str, request: Request, db: DbSession, user: CurrentUser
):
    ok = await AbService.delete_experiment(db, key)
    if not ok:
        raise AppException(40404, "实验不存在")
    await AuditService.log_staff(
        db,
        user=user,
        action="ab.experiment.delete",
        module="system",
        target_type="ab_experiment",
        target_id=None,
        detail={"key": key},
        request=request,
    )
    return success({"deleted": True, "key": key}, request=request)


@router.get("/{key}/report", dependencies=[require_permission("system:manage")])
async def experiment_report(
    key: str,
    request: Request,
    db: DbSession,
    date_from: str | None = None,
    date_to: str | None = None,
):
    try:
        report = await AbService.experiment_report(
            db,
            key,
            _parse_date(date_from),
            _parse_date(date_to, end=True),
        )
    except ValueError as e:
        raise AppException(40404, str(e)) from e
    return success(report, request=request)

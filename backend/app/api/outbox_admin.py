"""Outbox 死信运维：仅超级管理员可查询与人工重放。"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import calc_pages, success, serialize_dt, now_local
from app.models import OutboxEvent
from app.services.audit_service import AuditService
from app.services.outbox_worker import (
    _resolve_outbox_handler,
    requeue_dead_letter_outbox,
)

router = APIRouter(prefix="/admin/outbox", tags=["系统运维-Outbox"])


def _safe_error(value: str | None) -> str:
    """隐藏异常文本中常见的凭据形式，避免运维列表泄露 token/密码。"""
    text = (value or "")[:1000]
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+", r"\1***", text)
    text = re.sub(
        r'''(?i)(["']?[\w-]*(?:password|passwd|token|secret|api[_-]?key|auth[_-]?token)[\w-]*["']?\s*[:=]\s*["']?)[^"'\s,;}]+''',
        r"\1***",
        text,
    )
    return text[:500]


def _require_super_admin(user: CurrentUser) -> None:
    if getattr(getattr(user, "role", None), "code", None) != "SUPER_ADMIN":
        raise AppException(40300, "Outbox 死信仅超级管理员可操作", status_code=403)


def _ensure_handler_registry() -> None:
    """加载项目内置 handler 注册模块；不执行任何 handler。"""
    for module in (
        "app.services.paid_side_effects",
        "app.services.delivery_sync_handlers",
        "app.services.menu_cache_handlers",
        "app.services.delivery_tasks",
    ):
        try:
            __import__(module)
        except Exception:
            # 若所需 handler 未注册，后续会明确拒绝重放；不能因无关模块加载失败阻塞全部列表。
            continue
    try:
        from app.services.reco_cache import register_reco_cache_outbox_handler
        register_reco_cache_outbox_handler()
    except Exception:
        pass


class OutboxReplayIn(BaseModel):
    reason: str = Field(..., min_length=8, max_length=500)
    confirmed_not_applied: bool = False


class OutboxResolveIn(BaseModel):
    reason: str = Field(..., min_length=8, max_length=500)


def _is_external_result_unknown(value: str | None) -> bool:
    return str(value or "").startswith("EXTERNAL_RESULT_UNKNOWN:")


def _require_external_result_replay_confirmation(
    value: str | None,
    confirmed_not_applied: bool,
    reason: str,
) -> None:
    unknown = _is_external_result_unknown(value)
    if unknown and (
        not confirmed_not_applied or "下游核实未执行" not in str(reason or "")
    ):
        raise AppException(
            40905,
            "该事件的下游执行结果未知。请先核实外卖平台/打印设备确实未执行，再明确确认后重放；若已执行，请使用“核实已成功”关闭死信。",
            status_code=409,
        )
    if confirmed_not_applied and not unknown:
        raise AppException(40906, "仅外部结果未知的死信需要该确认标记", status_code=409)


@router.get("/dead-letters", dependencies=[require_permission("system:manage")])
async def list_dead_letters(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    _require_super_admin(user)
    filters = (
        OutboxEvent.status == "FAILED",
        OutboxEvent.attempt_count >= OutboxEvent.max_attempts,
    )
    total = int((await db.execute(
        select(func.count()).select_from(OutboxEvent).where(*filters)
    )).scalar() or 0)
    rows = (await db.execute(
        select(OutboxEvent)
        .where(*filters)
        .order_by(OutboxEvent.updated_at.desc(), OutboxEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()
    return success({
        "items": [{
            "id": row.id,
            "aggregate_type": row.aggregate_type,
            "aggregate_id": row.aggregate_id,
            "event_kind": row.event_kind,
            "status": row.status,
            "attempt_count": row.attempt_count,
            "max_attempts": row.max_attempts,
            "last_error": _safe_error(row.last_error),
            "result_unknown": _is_external_result_unknown(row.last_error),
            "created_at": serialize_dt(row.created_at),
            "updated_at": serialize_dt(row.updated_at),
        } for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": calc_pages(total, page_size),
    }, request=request)


@router.post("/dead-letters/{outbox_id}/replay", dependencies=[require_permission("system:manage")])
async def replay_dead_letter(
    outbox_id: int,
    data: OutboxReplayIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    _require_super_admin(user)
    if len(data.reason.strip()) < 8:
        raise AppException(40012, "重放原因去除空格后至少需要 8 个字符", status_code=400)
    row = (await db.execute(
        select(OutboxEvent).where(OutboxEvent.id == outbox_id)
    )).scalar_one_or_none()
    if row is None:
        raise AppException(40401, "Outbox 事件不存在", status_code=404)
    if row.status != "FAILED" or int(row.attempt_count or 0) < int(row.max_attempts or 0):
        raise AppException(40901, "该事件不是已耗尽重试次数的死信，不能人工重放", status_code=409)
    _require_external_result_replay_confirmation(
        row.last_error, data.confirmed_not_applied, data.reason
    )

    try:
        payload = json.loads(row.payload) if row.payload else {}
        if not isinstance(payload, dict):
            raise ValueError("payload 必须是 JSON object")
    except (TypeError, ValueError) as exc:
        raise AppException(40902, "死信 payload 无效，拒绝重放", status_code=409) from exc

    _ensure_handler_registry()
    if _resolve_outbox_handler(row) is None:
        raise AppException(40903, "当前版本未注册该事件处理器，拒绝重放", status_code=409)

    # 保存重放前快照：SQLAlchemy ORM UPDATE 默认可能同步当前 identity map，
    # 若在 UPDATE 后再读 row.attempt_count，审计日志会错误记录为 0。
    previous_attempt_count = int(row.attempt_count or 0)
    previous_max_attempts = int(row.max_attempts or 0)
    previous_aggregate_type = row.aggregate_type
    previous_aggregate_id = row.aggregate_id
    previous_event_kind = row.event_kind
    previous_error = _safe_error(row.last_error)
    changed = await requeue_dead_letter_outbox(db, outbox_id)
    if not changed:
        await db.rollback()
        raise AppException(40904, "死信状态已变化，请刷新后重试", status_code=409)

    await AuditService.log_staff(
        db,
        user=user,
        action="system.outbox.dead_letter.replay",
        module="system",
        target_type="outbox_event",
        target_id=outbox_id,
        detail={
            "aggregate_type": previous_aggregate_type,
            "aggregate_id": previous_aggregate_id,
            "event_kind": previous_event_kind,
            "previous_attempt_count": previous_attempt_count,
            "max_attempts": previous_max_attempts,
            "previous_error": previous_error,
            "confirmed_not_applied": bool(data.confirmed_not_applied),
            "reason": data.reason.strip(),
        },
        request=request,
        store_id=None,
    )
    await db.commit()
    return success({"id": outbox_id, "status": "PENDING", "requeued": True}, request=request)


@router.post("/dead-letters/{outbox_id}/resolve", dependencies=[require_permission("system:manage")])
async def resolve_unknown_dead_letter(
    outbox_id: int,
    data: OutboxResolveIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """外部平台已确认执行成功时，关闭未知结果死信，避免误重放。"""
    _require_super_admin(user)
    if len(data.reason.strip()) < 8:
        raise AppException(40012, "核实原因去除空格后至少需要 8 个字符", status_code=400)
    row = (await db.execute(
        select(OutboxEvent).where(OutboxEvent.id == outbox_id)
    )).scalar_one_or_none()
    if row is None:
        raise AppException(40401, "Outbox 事件不存在", status_code=404)
    if (
        row.status != "FAILED"
        or int(row.attempt_count or 0) < int(row.max_attempts or 0)
        or not _is_external_result_unknown(row.last_error)
    ):
        raise AppException(40907, "该事件不是待核实的外部结果未知死信", status_code=409)

    previous_error = _safe_error(row.last_error)
    result = await db.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == int(outbox_id),
            OutboxEvent.status == "FAILED",
            OutboxEvent.attempt_count >= OutboxEvent.max_attempts,
            OutboxEvent.last_error.like("EXTERNAL_RESULT_UNKNOWN:%"),
        )
        .values(
            status="DONE",
            processed_at=now_local(),
            next_retry_at=None,
            locked_at=None,
            locked_by=None,
            last_error="EXTERNAL_RESULT_RECONCILED_SUCCESS",
        )
    )
    if int(result.rowcount or 0) != 1:
        await db.rollback()
        raise AppException(40908, "死信状态已变化，请刷新后重试", status_code=409)

    await AuditService.log_staff(
        db,
        user=user,
        action="system.outbox.dead_letter.resolve",
        module="system",
        target_type="outbox_event",
        target_id=outbox_id,
        detail={
            "aggregate_type": row.aggregate_type,
            "aggregate_id": row.aggregate_id,
            "event_kind": row.event_kind,
            "previous_error": previous_error,
            "resolution": "external_platform_confirmed_success",
            "reason": data.reason.strip(),
        },
        request=request,
        store_id=None,
    )
    await db.commit()
    return success({"id": outbox_id, "status": "DONE", "resolved": True}, request=request)

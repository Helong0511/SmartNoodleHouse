"""系统设置路由共用 helper。"""
from __future__ import annotations

from fastapi import Request

from app.api.deps import CurrentUser, DbSession


async def write_audit(
    db: DbSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    target_type: str,
    target_id: int | None,
    detail: dict,
) -> None:
    from app.services.audit_service import AuditService

    await AuditService.log_staff(
        db,
        user=user,
        action=action,
        module="settings",
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        request=request,
    )

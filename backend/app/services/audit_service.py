"""操作审计统一入口：写入 operation_logs，敏感字段脱敏。"""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OperationLog

_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|access_token|refresh_token|authorization|"
    r"api_key|apikey|private_key|credential|phone|mobile|id_card|feature)",
    re.IGNORECASE,
)


class AuditService:
    @staticmethod
    def redact(value: Any, *, depth: int = 0) -> Any:
        if depth > 6:
            return "***"
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                if _SENSITIVE_KEY_RE.search(str(k)):
                    out[str(k)] = "***"
                else:
                    out[str(k)] = AuditService.redact(v, depth=depth + 1)
            return out
        if isinstance(value, (list, tuple)):
            return [AuditService.redact(v, depth=depth + 1) for v in value]
        if isinstance(value, str) and len(value) > 2000:
            return value[:2000] + "…"
        return value

    @staticmethod
    def _client_meta(request: Request | None) -> tuple[str | None, str | None, str | None]:
        if request is None:
            return None, None, None
        ip = request.client.host if request.client else None
        ua = (request.headers.get("user-agent") or "")[:500] or None
        rid = getattr(request.state, "request_id", None)
        return ip, ua, rid

    @classmethod
    async def log(
        cls,
        db: AsyncSession,
        *,
        action: str,
        module: str,
        user_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        detail: Any = None,
        request: Request | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
    ) -> OperationLog:
        rip, rua, rrid = cls._client_meta(request)
        payload: str | None = None
        if detail is not None:
            if isinstance(detail, str):
                payload = detail[:4000]
            else:
                payload = json.dumps(cls.redact(detail), ensure_ascii=False, default=str)[:4000]
        row = OperationLog(
            user_id=user_id,
            action=action,
            module=module,
            target_type=target_type,
            target_id=target_id,
            ip=ip or rip,
            user_agent=user_agent or rua,
            request_id=request_id or rrid,
            detail=payload,
        )
        db.add(row)
        await db.flush()
        return row

    @classmethod
    async def log_login(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        username: str,
        success: bool,
        request: Request | None = None,
        extra: dict | None = None,
    ) -> OperationLog:
        return await cls.log(
            db,
            user_id=user_id if success else None,
            action="auth.login" if success else "auth.login_failed",
            module="auth",
            target_type="user",
            target_id=user_id,
            detail={"username": username, "success": success, **(extra or {})},
            request=request,
        )

    @classmethod
    async def log_refund(
        cls,
        db: AsyncSession,
        *,
        user_id: int | None,
        refund_no: str | None,
        payment_no: str | None = None,
        amount: Any = None,
        reason: str | None = None,
        request: Request | None = None,
        extra: dict | None = None,
    ) -> OperationLog:
        return await cls.log(
            db,
            user_id=user_id,
            action="payment.refund",
            module="payment",
            target_type="refund",
            target_id=None,
            detail={
                "refund_no": refund_no,
                "payment_no": payment_no,
                "amount": str(amount) if amount is not None else None,
                "reason": reason,
                **(extra or {}),
            },
            request=request,
        )

    @classmethod
    async def log_permission_change(
        cls,
        db: AsyncSession,
        *,
        operator_id: int,
        target_user_id: int,
        changes: dict,
        request: Request | None = None,
    ) -> OperationLog:
        return await cls.log(
            db,
            user_id=operator_id,
            action="user.permission_change",
            module="user",
            target_type="user",
            target_id=target_user_id,
            detail=changes,
            request=request,
        )

    @classmethod
    async def log_face_consent(
        cls,
        db: AsyncSession,
        *,
        customer_id: int,
        profile_id: int | None,
        request: Request | None = None,
        extra: dict | None = None,
    ) -> OperationLog:
        return await cls.log(
            db,
            user_id=None,
            action="face.consent",
            module="face",
            target_type="customer",
            target_id=customer_id,
            detail={"customer_id": customer_id, "profile_id": profile_id, **(extra or {})},
            request=request,
        )

    @classmethod
    async def log_face_revoke(
        cls,
        db: AsyncSession,
        *,
        customer_id: int,
        profile_ids: list[int],
        request: Request | None = None,
        operator_id: int | None = None,
        extra: dict | None = None,
    ) -> OperationLog:
        return await cls.log(
            db,
            user_id=operator_id,
            action="face.revoke",
            module="face",
            target_type="customer",
            target_id=customer_id,
            detail={
                "customer_id": customer_id,
                "profile_ids": profile_ids,
                **(extra or {}),
            },
            request=request,
        )

"""操作审计统一入口：写入 operation_logs，敏感字段脱敏。

分类（category）：
- STAFF：通用员工写操作
- STORE_ADMIN：店长专属写操作（修改门店信息/员工管理/桌台管理等）
- CASHIER：收银员专属写操作（开/结班、退款申请等）
- KITCHEN：厨房专属写操作（订单状态、厨房接管、补打票等）
- SYSTEM：系统级变更（业务参数、门店新建、外卖平台绑定、定时任务等）
- CUSTOMER：顾客自主操作（注册、绑手机号、人脸同意/撤销等）
- DANGER：可疑业务侧滥用（连续登录失败、权限越权尝试等）
- INTRUSION：服务器侧渗透/暴力破解/SQL注入/路径遍历等网络层威胁
"""
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

# 哨兵：用于区分 log_staff 中"未传 store_id"（自动推断）与"显式传 None"（跨店置空）
_UNSET = object()

# 角色→分类映射（仅用于 staff 侧自动归类）
_ROLE_CATEGORY_MAP = {
    "SUPER_ADMIN": "SYSTEM",
    "STORE_ADMIN": "STORE_ADMIN",
    "CASHIER": "CASHIER",
    "KITCHEN": "KITCHEN",
    "OPERATOR": "STAFF",
}


class AuditService:
    # 公开分类常量，便于外部引用
    CATEGORY_STAFF = "STAFF"
    CATEGORY_STORE_ADMIN = "STORE_ADMIN"
    CATEGORY_CASHIER = "CASHIER"
    CATEGORY_KITCHEN = "KITCHEN"
    CATEGORY_SYSTEM = "SYSTEM"
    CATEGORY_CUSTOMER = "CUSTOMER"
    CATEGORY_DANGER = "DANGER"
    CATEGORY_INTRUSION = "INTRUSION"

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
        # 与白名单/限流一致：走可信代理解析，避免反代场景记录到代理 IP
        from app.core.ip_allowlist import client_ip

        try:
            ip = client_ip(request) or None
        except Exception:
            ip = request.client.host if request.client else None
        ua = (request.headers.get("user-agent") or "")[:500] or None
        rid = getattr(request.state, "request_id", None)
        return ip, ua, rid

    @classmethod
    def category_for_user(cls, user) -> str:
        """根据 user.role.code 推断审计分类；未识别返回 STAFF。"""
        if user is None:
            return cls.CATEGORY_STAFF
        code = getattr(getattr(user, "role", None), "code", None)
        return _ROLE_CATEGORY_MAP.get(code, cls.CATEGORY_STAFF)

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
        category: str | None = None,
        store_id: int | None = None,
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
            category=category or cls.CATEGORY_STAFF,
            store_id=store_id,
        )
        db.add(row)
        await db.flush()
        return row

    @classmethod
    async def log_staff(
        cls,
        db: AsyncSession,
        *,
        user,
        action: str,
        module: str,
        target_type: str | None = None,
        target_id: int | None = None,
        detail: Any = None,
        request: Request | None = None,
        category: str | None = None,
        store_id: Any = _UNSET,
    ) -> OperationLog:
        """员工写操作便捷入口：自动按角色推断分类、按 user.store_id 推断门店归属。

        store_id 语义：
        - 未传（默认 _UNSET）：自动按 user.store_id 推断
        - 显式传 None：表示跨店/系统级操作，强制置空
        - 显式传 int：使用该值
        """
        uid = int(user.id) if getattr(user, "id", None) else None
        cat = category or cls.category_for_user(user)
        if store_id is _UNSET:
            sid = int(user.store_id) if getattr(user, "store_id", None) is not None else None
        else:
            sid = store_id
        return await cls.log(
            db,
            user_id=uid,
            action=action,
            module=module,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            request=request,
            category=cat,
            store_id=sid,
        )

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
        store_id: int | None = None,
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
            category=cls.CATEGORY_SYSTEM if success else cls.CATEGORY_DANGER,
            store_id=store_id,
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
        store_id: int | None = None,
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
            category=cls.CATEGORY_STAFF,
            store_id=store_id,
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
            category=cls.CATEGORY_SYSTEM,
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
            category=cls.CATEGORY_CUSTOMER,
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
            category=cls.CATEGORY_CUSTOMER if operator_id is None else cls.CATEGORY_STAFF,
        )

    @classmethod
    async def log_intrusion(
        cls,
        db: AsyncSession,
        *,
        reason: str,
        request: Request | None = None,
        path: str | None = None,
        user_id: int | None = None,
        extra: dict | None = None,
    ) -> OperationLog:
        """记录服务器侧渗透/暴力破解/SQL注入/路径遍历等危险请求。"""
        detail = {"reason": reason, "path": path, **(extra or {})}
        return await cls.log(
            db,
            user_id=user_id,
            action="intrusion.detected",
            module="security",
            target_type="request",
            target_id=None,
            detail=detail,
            request=request,
            category=cls.CATEGORY_INTRUSION,
        )

    @classmethod
    async def log_danger(
        cls,
        db: AsyncSession,
        *,
        action: str,
        detail: Any,
        request: Request | None = None,
        user_id: int | None = None,
    ) -> OperationLog:
        """记录业务侧可疑滥用（如权限越权尝试、敏感数据枚举）。"""
        return await cls.log(
            db,
            user_id=user_id,
            action=action,
            module="security",
            target_type=None,
            target_id=None,
            detail=detail,
            request=request,
            category=cls.CATEGORY_DANGER,
        )

"""临时授权：生效期内将被委托人权限与角色权限合并。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import now_local
from app.models import RoleDelegation


class RoleDelegationService:
    @staticmethod
    async def active_permission_codes(db: AsyncSession, user_id: int) -> set[str]:
        now = now_local()
        rows = list(
            (
                await db.execute(
                    select(RoleDelegation).where(
                        RoleDelegation.delegatee_user_id == user_id,
                        RoleDelegation.status == "ACTIVE",
                        RoleDelegation.starts_at <= now,
                        RoleDelegation.ends_at > now,
                    )
                )
            ).scalars().all()
        )
        out: set[str] = set()
        for row in rows:
            codes = row.permission_codes or []
            if isinstance(codes, list):
                out.update(str(c) for c in codes if c)
        return out

    @staticmethod
    async def expire_stale(db: AsyncSession) -> int:
        now = now_local()
        rows = list(
            (
                await db.execute(
                    select(RoleDelegation).where(
                        RoleDelegation.status == "ACTIVE",
                        RoleDelegation.ends_at <= now,
                    )
                )
            ).scalars().all()
        )
        for row in rows:
            row.status = "EXPIRED"
        if rows:
            await db.flush()
        return len(rows)

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        grantor_user_id: int,
        delegatee_user_id: int,
        permission_codes: list[str],
        starts_at: datetime,
        ends_at: datetime,
        reason: str | None = None,
    ) -> RoleDelegation:
        if ends_at <= starts_at:
            raise ValueError("ends_at 必须晚于 starts_at")
        codes = sorted({str(c).strip() for c in permission_codes if str(c).strip()})
        if not codes:
            raise ValueError("permission_codes 不能为空")
        row = RoleDelegation(
            grantor_user_id=grantor_user_id,
            delegatee_user_id=delegatee_user_id,
            permission_codes=codes,
            starts_at=starts_at,
            ends_at=ends_at,
            status="ACTIVE",
            reason=(reason or "")[:255] or None,
        )
        db.add(row)
        await db.flush()
        return row

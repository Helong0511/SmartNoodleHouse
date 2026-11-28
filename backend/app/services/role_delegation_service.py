"""临时授权：生效期内将被委托人权限与角色权限合并。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import now_local
from app.models import Permission, Role, RoleDelegation, RolePermission, User


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
        if int(grantor_user_id) == int(delegatee_user_id):
            raise ValueError("不能给自己委派")
        if ends_at <= starts_at:
            raise ValueError("ends_at 必须晚于 starts_at")
        codes = sorted({str(c).strip() for c in permission_codes if str(c).strip()})
        if not codes:
            raise ValueError("permission_codes 不能为空")
        if "system:manage" in codes:
            raise ValueError("system:manage 不可通过临时委派授予")

        # Service-level authorization is mandatory: callers other than the HTTP
        # endpoint must not be able to bypass the delegation ceiling.
        locked_users = (
            await db.execute(
                select(User)
                .where(User.id.in_(sorted({int(grantor_user_id), int(delegatee_user_id)})))
                .order_by(User.id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars().all()
        users = {int(item.id): item for item in locked_users}
        grantor = users.get(int(grantor_user_id))
        delegatee = users.get(int(delegatee_user_id))
        if grantor is None or int(grantor.status or 0) != 1:
            raise ValueError("委托人不存在或已停用")
        if delegatee is None or int(delegatee.status or 0) != 1:
            raise ValueError("被委托人不存在或已停用")

        grantor_role = await db.get(Role, int(grantor.role_id))
        if grantor_role is None:
            raise ValueError("委托人角色不存在")
        if grantor_role.code == "SUPER_ADMIN":
            grantable_codes = set(
                (await db.execute(select(Permission.code))).scalars().all()
            )
        else:
            if (
                not grantor.store_id
                or delegatee.store_id is None
                or int(delegatee.store_id) != int(grantor.store_id)
            ):
                raise ValueError("只能委派本店员工")
            grantable_codes = set(
                (
                    await db.execute(
                        select(Permission.code)
                        .join(RolePermission, RolePermission.permission_id == Permission.id)
                        .where(RolePermission.role_id == int(grantor.role_id))
                        .with_for_update()
                    )
                ).scalars().all()
            )
        if not set(codes).issubset(grantable_codes):
            raise ValueError("不能委派自身角色直接持有的权限以外的权限")

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

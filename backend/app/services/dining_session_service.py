"""就餐会话：扫码绑桌建立 ACTIVE session，过期/换桌后不再沿用旧桌。"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import now_local
from app.models import Customer, DiningSession, DiningTable

# 堂食会话默认有效期（小时）；过期后须重新扫码
DEFAULT_SESSION_HOURS = 4


class DiningSessionService:
    @staticmethod
    def _session_payload(session: DiningSession | None) -> dict | None:
        if not session:
            return None
        return {
            "id": session.id,
            "customer_id": session.customer_id,
            "store_id": session.store_id,
            "table_id": session.table_id,
            "table_token": session.table_token,
            "status": session.status,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "last_active_at": session.last_active_at.isoformat() if session.last_active_at else None,
            "expires_at": session.expires_at.isoformat() if session.expires_at else None,
        }

    @staticmethod
    async def expire_stale(db: AsyncSession, customer_id: int) -> None:
        now = now_local()
        await db.execute(
            update(DiningSession)
            .where(
                DiningSession.customer_id == int(customer_id),
                DiningSession.status == "ACTIVE",
                DiningSession.expires_at <= now,
            )
            .values(status="EXPIRED", ended_at=now)
        )

    @staticmethod
    async def get_active(db: AsyncSession, customer_id: int) -> DiningSession | None:
        await DiningSessionService.expire_stale(db, customer_id)
        now = now_local()
        return (
            await db.execute(
                select(DiningSession)
                .where(
                    DiningSession.customer_id == int(customer_id),
                    DiningSession.status == "ACTIVE",
                    DiningSession.expires_at > now,
                )
                .order_by(DiningSession.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def start_or_refresh(
        db: AsyncSession,
        *,
        customer_id: int,
        table: DiningTable,
        table_token: str | None = None,
        hours: int = DEFAULT_SESSION_HOURS,
    ) -> DiningSession:
        """扫码进入：同桌续期；换桌则结束旧会话并开新会话。"""
        # 以顾客行为锁串行化换桌，避免并发扫码留下多个 ACTIVE 会话。
        locked_customer = (
            await db.execute(
                select(Customer).where(Customer.id == int(customer_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if not locked_customer or int(locked_customer.status or 0) != 1:
            raise AppException(40102, "顾客不存在或已禁用", status_code=401)
        await DiningSessionService.expire_stale(db, customer_id)
        now = now_local()
        expires = now + timedelta(hours=max(1, int(hours or DEFAULT_SESSION_HOURS)))
        token = (table_token or getattr(table, "qr_token", None) or "").strip() or None

        active = await DiningSessionService.get_active(db, customer_id)
        if active and int(active.table_id) == int(table.id) and int(active.store_id) == int(table.store_id):
            active.last_active_at = now
            active.expires_at = expires
            if token:
                active.table_token = token
            await db.flush()
            return active

        if active:
            active.status = "CANCELLED"
            active.ended_at = now
            await db.flush()

        session = DiningSession(
            customer_id=int(customer_id),
            store_id=int(table.store_id),
            table_id=int(table.id),
            table_token=token,
            status="ACTIVE",
            started_at=now,
            last_active_at=now,
            expires_at=expires,
        )
        db.add(session)
        await db.flush()
        return session

    @staticmethod
    async def touch(db: AsyncSession, session_id: int | None) -> None:
        if not session_id:
            return
        session = await db.get(DiningSession, int(session_id))
        if not session or session.status != "ACTIVE":
            return
        now = now_local()
        if session.expires_at and session.expires_at <= now:
            session.status = "EXPIRED"
            session.ended_at = now
            await db.flush()
            return
        session.last_active_at = now
        await db.flush()

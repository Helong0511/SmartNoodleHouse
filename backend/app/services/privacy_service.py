"""隐私合规服务（Task 13）：

- 个性化推荐开关读写（recommend_opt_in）
- 口味画像可读视图
- 清除画像派生数据（行为事件 + 画像 + 评价 + customer_preferences），订单本体保留
- 版本化同意留痕
- 超期行为事件定时清理（默认 90 天，system_settings 可调）
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Customer,
    CustomerBehaviorEvent,
    CustomerPreference,
    CustomerTasteProfile,
    ProductFeedback,
)
from app.services.audit_service import AuditService
from app.core.response import serialize_dt

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 90
CONSENT_VERSION = "1.0"


class PrivacyService:
    @staticmethod
    async def get_privacy_settings(db: AsyncSession, customer: Customer) -> dict[str, Any]:
        """返回个性化推荐开关 + 同意版本/时间。"""
        return {
            "recommend_opt_in": int(customer.recommend_opt_in or 0),
            "consent_version": customer.consent_version,
            "consented_at": serialize_dt(customer.recommend_consent_at)
            if customer.recommend_consent_at
            else None,
            "retention_days": DEFAULT_RETENTION_DAYS,
        }

    @classmethod
    async def update_opt_in(
        cls,
        db: AsyncSession,
        customer: Customer,
        opt_in: int,
        request: Any = None,
    ) -> dict[str, Any]:
        """更新个性化推荐开关；关闭时立即丢弃未入库的事件队列（前端职责），后端 ingest 已拒收。"""
        opt_in = 1 if opt_in else 0
        old = int(customer.recommend_opt_in or 0)
        customer.recommend_opt_in = opt_in
        await db.flush()
        await AuditService.log(
            db,
            action="privacy.update_opt_in",
            module="privacy",
            target_type="customer",
            target_id=int(customer.id),
            detail={"old": old, "new": opt_in},
            request=request,
            category=AuditService.CATEGORY_CUSTOMER,
        )
        return await cls.get_privacy_settings(db, customer)

    @classmethod
    async def get_taste_profile_view(
        cls, db: AsyncSession, customer: Customer
    ) -> dict[str, Any]:
        """口味画像可读视图：按维度分组，含规格偏好。"""
        profiles = (
            await db.execute(
                select(CustomerTasteProfile)
                .where(CustomerTasteProfile.customer_id == int(customer.id))
                .order_by(CustomerTasteProfile.score.desc())
            )
        ).scalars().all()

        by_dim: dict[str, list[dict[str, Any]]] = {
            "CATEGORY": [],
            "PRODUCT": [],
            "TASTE_TAG": [],
        }
        for p in profiles:
            dim = p.dim_type
            if dim not in by_dim:
                by_dim[dim] = []
            by_dim[dim].append(
                {
                    "target_key": p.target_key,
                    "target_name": p.target_name,
                    "score": float(p.score),
                    "event_count": p.event_count,
                    "last_event_at": serialize_dt(p.last_event_at) if p.last_event_at else None,
                }
            )

        prefs = (
            await db.execute(
                select(CustomerPreference)
                .where(CustomerPreference.customer_id == int(customer.id))
                .order_by(CustomerPreference.preference_score.desc())
                .limit(20)
            )
        ).scalars().all()
        spec_prefs = [
            {
                "category": p.category,
                "option_code": p.option_code,
                "option_name": p.option_name,
                "score": float(p.preference_score) if p.preference_score is not None else None,
                "order_count": p.order_count,
                "last_used_at": serialize_dt(p.last_used_at) if p.last_used_at else None,
            }
            for p in prefs
        ]

        return {
            "customer_id": int(customer.id),
            "dimensions": by_dim,
            "spec_preferences": spec_prefs,
        }

    @classmethod
    async def clear_taste_profile(
        cls,
        db: AsyncSession,
        customer: Customer,
        request: Any = None,
    ) -> dict[str, Any]:
        """清除画像派生数据：行为事件 + 画像 + 评价 + customer_preferences。
        订单本体保留；写审计日志。"""
        cid = int(customer.id)
        events_n = (
            await db.execute(
                select(CustomerBehaviorEvent.id).where(CustomerBehaviorEvent.customer_id == cid)
            )
        ).all()
        events_count = len(events_n)

        await db.execute(
            delete(CustomerBehaviorEvent).where(CustomerBehaviorEvent.customer_id == cid)
        )
        await db.execute(
            delete(CustomerTasteProfile).where(CustomerTasteProfile.customer_id == cid)
        )
        await db.execute(
            delete(ProductFeedback).where(ProductFeedback.customer_id == cid)
        )
        await db.execute(
            delete(CustomerPreference).where(CustomerPreference.customer_id == cid)
        )
        await db.flush()

        await AuditService.log(
            db,
            action="privacy.clear_taste_profile",
            module="privacy",
            target_type="customer",
            target_id=cid,
            detail={
                "events_deleted": events_count,
                "cleared_tables": [
                    "customer_behavior_events",
                    "customer_taste_profiles",
                    "product_feedbacks",
                    "customer_preferences",
                ],
            },
            request=request,
            category=AuditService.CATEGORY_CUSTOMER,
        )

        return {
            "cleared": True,
            "events_deleted": events_count,
            "tables": [
                "customer_behavior_events",
                "customer_taste_profiles",
                "product_feedbacks",
                "customer_preferences",
            ],
        }

    @classmethod
    async def record_consent(
        cls,
        db: AsyncSession,
        customer: Customer,
        version: str | None = None,
        request: Any = None,
    ) -> dict[str, Any]:
        """记录版本化同意留痕。"""
        ver = (version or CONSENT_VERSION).strip()[:40]
        now = datetime.now(timezone.utc)
        old_ver = customer.consent_version
        customer.consent_version = ver
        customer.recommend_consent_at = now
        customer.recommend_opt_in = 1
        await db.flush()

        await AuditService.log(
            db,
            action="privacy.consent",
            module="privacy",
            target_type="customer",
            target_id=int(customer.id),
            detail={"version": ver, "old_version": old_ver, "consented_at": serialize_dt(now)},
            request=request,
            category=AuditService.CATEGORY_CUSTOMER,
        )

        return {
            "consent_version": ver,
            "consented_at": serialize_dt(now),
            "recommend_opt_in": 1,
        }

    @classmethod
    async def cleanup_expired_events(cls, db: AsyncSession, retention_days: int | None = None) -> int:
        """定时清理超期行为事件（leader fence 由 scheduler 包裹）。"""
        days = retention_days or DEFAULT_RETENTION_DAYS
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = await db.execute(
            delete(CustomerBehaviorEvent).where(CustomerBehaviorEvent.created_at < cutoff)
        )
        n = result.rowcount or 0
        await db.commit()
        if n:
            logger.info("Expired behavior events cleaned: %s rows (retention=%sd)", n, days)
        return n

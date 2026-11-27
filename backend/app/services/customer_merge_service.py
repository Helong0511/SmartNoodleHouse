"""顾客合并：source → target（事务内迁移 FK / 积分 / 券 / 人脸等）。

Merge policy
============
1. 目标顾客保留（status 不变）；源顾客 status=0，token_version+1，写 AuditService 审计
   （before/after 含脱敏手机号）。
2. 积分：target.points = target.points + source.points（双方 FOR UPDATE），写 MERGE 流水，再
   CustomerService.recalculate_member_level。
3. 券：全部迁入目标（同模板可多张）；不再按模板去重作废。
4. 平台账号（openid / alipay_user_id 唯一）：目标已存在同 openid/userid 则删除源账号
   行；否则改 customer_id 到目标。
5. 偏好 UNIQUE(customer, category, option_code)：冲突保留更高 preference_score
   （同分保留目标，累加 order_count）。
6. FaceProfile：全部挂到目标后，仅保留最新一份 AUTHORIZED，其余 REVOKED。
7. Order / Payment 的 customer_id 改挂目标；PaymentRefund 无 customer_id 列故跳过。
8. WelcomeLog 改挂目标；积分/储值流水保留原始快照归属，并记录当前资产归属与原账户。
9. 储值：本金/赠送金并入目标，写 MERGE 流水；balance_ledger / recharge_orders 改挂目标；储值流水保留原始账户标识。
10. 公益身份 CustomerSocialIdentity / 核销台账 social_benefit_redemptions 改挂目标；
    同 identity_code 若目标已有 ACTIVE 则源侧 ACTIVE/PENDING 改为 REVOKED，避免双生效。
11. 推荐行为事件迁移到目标；CustomerTasteProfile 按最近事件时间衰减后合并 raw_weight/event_count；
    收藏、生命周期快照、发票申请、客群成员、营销任务/消息历史/活动归因及同桌共享车
    的主控/成员/草稿行归属一并迁移并处理唯一键冲突。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import Request
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import now_local
from app.models import (
    BalanceLedger,
    CampaignAttribution,
    Customer,
    CustomerAlipayAccount,
    CustomerBehaviorEvent,
    CustomerCoupon,
    CustomerFavoriteProduct,
    CustomerIdentity,
    CustomerLifecycle,
    CustomerPreference,
    CustomerSegmentMember,
    CustomerSocialIdentity,
    CustomerTasteProfile,
    CustomerWechatAccount,
    DiningSession,
    FaceProfile,
    InvoiceRequest,
    MarketingMessageLog,
    MarketingTask,
    Order,
    Payment,
    PointLedger,
    RechargeOrder,
    SharedCartItem,
    SocialBenefitRedemption,
    TableParty,
    TablePartyMember,
    WelcomeLog,
)
from app.core.response import money, serialize_dt
from app.services.audit_service import AuditService
from app.services.customer_service import CustomerService


def _mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    s = str(phone).strip()
    if len(s) < 7:
        return "***"
    return f"{s[:3]}****{s[-4:]}"


def _customer_snapshot(c: Customer) -> dict[str, Any]:
    return {
        "id": c.id,
        "customer_no": c.customer_no,
        "phone": _mask_phone(c.phone),
        "points": int(c.points or 0),
        "balance_principal": str(money(getattr(c, "balance_principal", 0) or 0)),
        "balance_gift": str(money(getattr(c, "balance_gift", 0) or 0)),
        "member_level_id": c.member_level_id,
        "status": int(c.status or 0),
        "last_order_at": serialize_dt(c.last_order_at) if c.last_order_at else None,
        "nickname": c.nickname,
        "display_name": c.display_name,
    }


class CustomerMergeService:
    @classmethod
    async def merge(
        cls,
        db: AsyncSession,
        *,
        target_customer_id: int,
        source_customer_id: int,
        reason: str | None = None,
        operator_id: int | None = None,
        request: Request | None = None,
        actor_store_id: int | None = None,
    ) -> dict[str, Any]:
        if int(target_customer_id) == int(source_customer_id):
            raise AppException(40061, "不能合并同一顾客")

        # 按 id 升序加锁，避免死锁
        ids = sorted([int(target_customer_id), int(source_customer_id)])
        locked = (
            await db.execute(select(Customer).where(Customer.id.in_(ids)).order_by(Customer.id).with_for_update())
        ).scalars().all()
        by_id = {int(c.id): c for c in locked}
        target = by_id.get(int(target_customer_id))
        source = by_id.get(int(source_customer_id))
        if not target or not source:
            raise AppException(40461, "顾客不存在", status_code=404)
        if int(source.status or 0) == 0:
            raise AppException(40062, "源顾客已禁用/已合并")
        if actor_store_id is not None:
            sid = int(actor_store_id)
            foreign_order = (
                await db.execute(
                    select(Order.id)
                    .where(
                        Order.customer_id == source.id,
                        or_(Order.store_id.is_(None), Order.store_id != sid),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            foreign_recharge = (
                await db.execute(
                    select(RechargeOrder.id)
                    .where(
                        RechargeOrder.customer_id == source.id,
                        or_(RechargeOrder.store_id.is_(None), RechargeOrder.store_id != sid),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            foreign_wallet_ledger = (
                await db.execute(
                    select(BalanceLedger.id)
                    .where(
                        BalanceLedger.customer_id == source.id,
                        or_(BalanceLedger.store_id.is_(None), BalanceLedger.store_id != sid),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if foreign_order or foreign_recharge or foreign_wallet_ledger:
                raise AppException(40301, "源顾客存在其他门店或门店归属未知的订单/充值/储值流水，请由总部账号核实后合并")

        before = {
            "target": _customer_snapshot(target),
            "source": _customer_snapshot(source),
            "reason": (reason or "").strip() or None,
        }

        # P1-3 汇总订单、充值、储值流水及顾客档案中的门店归属，避免审计漏报跨店数据。
        source_store_ids = await cls._collect_source_store_ids(db, source)
        # NULL 门店归属不能伪装成任一门店；总部合并也必须显式记录未知范围。
        unknown_store_scope = await cls._collect_unknown_source_store_scope(db, source)

        stats: dict[str, Any] = {
            "wechat_moved": 0,
            "wechat_dropped": 0,
            "alipay_moved": 0,
            "alipay_dropped": 0,
            "identities_moved": 0,
            "dining_sessions": 0,
            "orders": 0,
            "payments": 0,
            "coupons_moved": 0,
            "coupons_expired_dup": 0,
            "preferences_moved": 0,
            "preferences_merged": 0,
            "taste_events_moved": 0,
            "taste_events_deleted": 0,
            "taste_profiles_moved": 0,
            "taste_profiles_merged": 0,
            "taste_profiles_deleted": 0,
            "favorites_moved": 0,
            "favorites_deduplicated": 0,
            "lifecycle_rows_moved": 0,
            "lifecycle_rows_merged": 0,
            "invoice_requests_moved": 0,
            "segment_members_moved": 0,
            "segment_members_deduplicated": 0,
            "marketing_tasks_moved": 0,
            "marketing_logs_moved": 0,
            "campaign_attributions_moved": 0,
            "table_party_hosts_moved": 0,
            "table_party_members_moved": 0,
            "table_party_members_closed": 0,
            "shared_cart_items_moved": 0,
            "faces_revoked": 0,
            "welcome_logs": 0,
            "point_ledgers": 0,
            "points_added": 0,
            "balance_principal_added": "0.00",
            "balance_gift_added": "0.00",
            "balance_ledgers": 0,
            "recharge_orders": 0,
            "social_identities_moved": 0,
            "social_identities_revoked": 0,
            "social_redemptions": 0,
        }

        await cls._merge_platform_accounts(db, source.id, target.id, stats)
        await cls._merge_identities(db, source.id, target.id, stats)
        await cls._merge_dining_sessions(db, source.id, target.id, stats)
        await cls._reassign_orders_payments(db, source.id, target.id, stats)
        await cls._merge_coupons(db, source.id, target.id, stats)
        await cls._merge_preferences(db, source.id, target.id, stats)
        await cls._merge_customer_profiles_and_relations(
            db,
            source.id,
            target.id,
            stats,
            source_opt_in=bool(getattr(source, "recommend_opt_in", 0)),
            target_opt_in=bool(getattr(target, "recommend_opt_in", 0)),
        )
        await cls._merge_table_party_memberships(db, source.id, target.id, stats)
        await cls._merge_faces(db, source.id, target.id, stats)
        await cls._merge_social_benefits(db, source.id, target.id, stats)
        await cls._reassign_simple(
            db, WelcomeLog, source.id, target.id, "welcome_logs", stats
        )
        await cls._reassign_ledger(
            db, PointLedger, source.id, target.id, "point_ledgers", stats
        )
        await cls._merge_wallet(db, source, target, stats)
        await cls._merge_points_and_meta(db, source, target, stats)

        source.status = 0
        source.account_status = "DISABLED"
        source.phone = None
        source.phone_verified = 0
        source.token_version = int(getattr(source, "token_version", 0) or 0) + 1
        await db.flush()

        after = {
            "target": _customer_snapshot(target),
            "source": _customer_snapshot(source),
            "stats": stats,
            "source_store_ids": source_store_ids,
            "unknown_store_scope": unknown_store_scope,
            "cross_store": len(source_store_ids) > 1,
        }
        await AuditService.log(
            db,
            user_id=operator_id,
            action="customer.merge",
            module="customer",
            target_type="customer",
            target_id=target.id,
            detail={"before": before, "after": after},
            request=request,
        )
        await db.flush()
        return {
            "target_customer_id": target.id,
            "source_customer_id": source.id,
            "stats": stats,
            "target": after["target"],
            "source_store_ids": source_store_ids,
            "unknown_store_scope": unknown_store_scope,
            "cross_store": len(source_store_ids) > 1,
        }


    @classmethod
    async def _collect_unknown_source_store_scope(
        cls, db: AsyncSession, source: Customer
    ) -> dict[str, int]:
        """统计来源顾客门店归属未知的业务记录，供总部合并审计追溯。"""
        unknown: dict[str, int] = {}
        for key, model in (
            ("orders", Order),
            ("recharge_orders", RechargeOrder),
            ("balance_ledgers", BalanceLedger),
        ):
            count = (
                await db.execute(
                    select(func.count(model.id)).where(
                        model.customer_id == int(source.id),
                        model.store_id.is_(None),
                    )
                )
            ).scalar_one()
            if int(count or 0) > 0:
                unknown[key] = int(count)
        return unknown

    @classmethod
    async def _collect_source_store_ids(cls, db: AsyncSession, source: Customer) -> list[int]:
        store_ids = {
            int(value)
            for value in (
                getattr(source, "registered_store_id", None),
                getattr(source, "first_seen_store_id", None),
            )
            if value is not None
        }
        for model in (Order, RechargeOrder, BalanceLedger):
            values = (
                await db.execute(
                    select(model.store_id)
                    .where(model.customer_id == int(source.id), model.store_id.is_not(None))
                    .distinct()
                )
            ).scalars().all()
            store_ids.update(int(value) for value in values if value is not None)
        return sorted(store_ids)

    @classmethod
    async def _merge_platform_accounts(
        cls, db: AsyncSession, source_id: int, target_id: int, stats: dict
    ) -> None:
        target_openids = set(
            (
                await db.execute(
                    select(CustomerWechatAccount.openid).where(
                        CustomerWechatAccount.customer_id == target_id
                    )
                )
            ).scalars().all()
        )
        for row in (
            await db.execute(
                select(CustomerWechatAccount).where(CustomerWechatAccount.customer_id == source_id)
            )
        ).scalars().all():
            if row.openid in target_openids:
                await db.delete(row)
                stats["wechat_dropped"] += 1
            else:
                row.customer_id = target_id
                target_openids.add(row.openid)
                stats["wechat_moved"] += 1

        target_uids = set(
            (
                await db.execute(
                    select(CustomerAlipayAccount.alipay_user_id).where(
                        CustomerAlipayAccount.customer_id == target_id
                    )
                )
            ).scalars().all()
        )
        for row in (
            await db.execute(
                select(CustomerAlipayAccount).where(CustomerAlipayAccount.customer_id == source_id)
            )
        ).scalars().all():
            if row.alipay_user_id in target_uids:
                await db.delete(row)
                stats["alipay_dropped"] += 1
            else:
                row.customer_id = target_id
                target_uids.add(row.alipay_user_id)
                stats["alipay_moved"] += 1
        await db.flush()

    @classmethod
    async def _reassign_orders_payments(
        cls, db: AsyncSession, source_id: int, target_id: int, stats: dict
    ) -> None:
        r1 = await db.execute(
            update(Order).where(Order.customer_id == source_id).values(customer_id=target_id)
        )
        stats["orders"] = int(r1.rowcount or 0)
        r2 = await db.execute(
            update(Payment).where(Payment.customer_id == source_id).values(customer_id=target_id)
        )
        stats["payments"] = int(r2.rowcount or 0)
        await db.flush()

    @classmethod
    async def _merge_identities(
        cls, db: AsyncSession, source_id: int, target_id: int, stats: dict
    ) -> None:
        rows = list(
            (
                await db.execute(
                    select(CustomerIdentity)
                    .where(CustomerIdentity.customer_id == source_id)
                    .with_for_update()
                )
            ).scalars().all()
        )
        for row in rows:
            row.customer_id = target_id
            stats["identities_moved"] += 1
        await db.flush()

    @classmethod
    async def _merge_dining_sessions(
        cls, db: AsyncSession, source_id: int, target_id: int, stats: dict
    ) -> None:
        from app.core.response import now_local

        sessions = list(
            (
                await db.execute(
                    select(DiningSession)
                    .where(DiningSession.customer_id.in_((source_id, target_id)))
                    .with_for_update()
                )
            ).scalars().all()
        )
        for row in sessions:
            if int(row.customer_id) == int(source_id):
                row.customer_id = target_id
                stats["dining_sessions"] += 1

        active = [s for s in sessions if (s.status or "").upper() == "ACTIVE"]
        if len(active) > 1:
            active.sort(
                key=lambda s: (s.last_active_at or s.started_at, int(s.id or 0)),
                reverse=True,
            )
            ended_at = now_local()
            for stale in active[1:]:
                stale.status = "CANCELLED"
                stale.ended_at = stale.ended_at or ended_at
        await db.flush()

    @classmethod
    async def _merge_coupons(
        cls, db: AsyncSession, source_id: int, target_id: int, stats: dict
    ) -> None:
        # 同模板可多张：源侧券一律迁入目标
        for cc in (
            await db.execute(select(CustomerCoupon).where(CustomerCoupon.customer_id == source_id))
        ).scalars().all():
            cc.customer_id = target_id
            stats["coupons_moved"] += 1
        await db.flush()

    @classmethod
    async def _merge_preferences(
        cls, db: AsyncSession, source_id: int, target_id: int, stats: dict
    ) -> None:
        target_prefs = {
            (p.category, p.option_code): p
            for p in (
                await db.execute(
                    select(CustomerPreference).where(CustomerPreference.customer_id == target_id)
                )
            ).scalars().all()
        }
        for sp in (
            await db.execute(
                select(CustomerPreference).where(CustomerPreference.customer_id == source_id)
            )
        ).scalars().all():
            key = (sp.category, sp.option_code)
            tp = target_prefs.get(key)
            if not tp:
                sp.customer_id = target_id
                target_prefs[key] = sp
                stats["preferences_moved"] += 1
                continue
            src_score = Decimal(str(sp.preference_score or 0))
            tgt_score = Decimal(str(tp.preference_score or 0))
            if src_score > tgt_score:
                tp.preference_score = src_score
                if sp.option_name and not tp.option_name:
                    tp.option_name = sp.option_name
            tp.order_count = int(tp.order_count or 0) + int(sp.order_count or 0)
            if sp.last_used_at and (not tp.last_used_at or sp.last_used_at > tp.last_used_at):
                tp.last_used_at = sp.last_used_at
            await db.delete(sp)
            stats["preferences_merged"] += 1
        await db.flush()

    @classmethod
    async def _merge_customer_profiles_and_relations(
        cls,
        db: AsyncSession,
        source_id: int,
        target_id: int,
        stats: dict,
        *,
        source_opt_in: bool,
        target_opt_in: bool,
    ) -> None:
        """按双方推荐同意状态迁移画像，再合并收藏、生命周期及营销关联。"""
        # 推荐隐私同意不随账号合并自动继承：仅双方均明确 opt-in 时合并事件/画像。
        # 先在顾客协调锁内 fold 两侧待处理事件，避免源事件早于目标画像水位而被漏学。
        profile_customer_ids = (
            ([int(source_id)] if source_opt_in else [])
            + ([int(target_id)] if target_opt_in else [])
        )
        if profile_customer_ids:
            from app.services.taste_learning_service import TasteLearningService

            for customer_id in profile_customer_ids:
                for _ in range(20):
                    learned = await TasteLearningService.learn_from_events(db, customer_id)
                    if learned <= 0:
                        break
                else:
                    raise AppException(
                        40964,
                        "顾客推荐行为事件积压过多，请稍后重试合并，避免丢失推荐画像",
                    )

        if source_opt_in and target_opt_in:
            await cls._reassign_simple(
                db, CustomerBehaviorEvent, source_id, target_id, "taste_events_moved", stats
            )
        else:
            # 源账户未同意时，不把其行为导入目标；目标未同意时双方画像/事件均清除。
            delete_customer_ids = (
                [int(source_id), int(target_id)] if not target_opt_in else [int(source_id)]
            )
            event_result = await db.execute(
                delete(CustomerBehaviorEvent).where(
                    CustomerBehaviorEvent.customer_id.in_(delete_customer_ids)
                )
            )
            profile_result = await db.execute(
                delete(CustomerTasteProfile).where(
                    CustomerTasteProfile.customer_id.in_(delete_customer_ids)
                )
            )
            stats["taste_events_deleted"] += int(event_result.rowcount or 0)
            stats["taste_profiles_deleted"] += int(profile_result.rowcount or 0)

        # CustomerTasteProfile 是增量画像，不能只改 customer_id 后覆盖目标。
        # 两侧 raw_weight 按时间衰减到较新的事件时间，再合并事件数与分数。
        from app.services.taste_learning_service import _decay_factor, _logistic, _quantize

        source_profiles = (
            list(
                (
                    await db.execute(
                        select(CustomerTasteProfile)
                        .where(CustomerTasteProfile.customer_id == int(source_id))
                        .order_by(CustomerTasteProfile.id)
                        .with_for_update()
                    )
                ).scalars().all()
            )
            if source_opt_in and target_opt_in
            else []
        )
        target_profiles = list(
            (
                await db.execute(
                    select(CustomerTasteProfile)
                    .where(CustomerTasteProfile.customer_id == int(target_id))
                    .order_by(CustomerTasteProfile.id)
                    .with_for_update()
                )
            ).scalars().all()
        )
        target_by_key = {
            (row.dim_type, row.target_key): row for row in target_profiles
        }
        for source_row in source_profiles:
            key = (source_row.dim_type, source_row.target_key)
            target_row = target_by_key.get(key)
            if target_row is None:
                source_row.customer_id = int(target_id)
                target_by_key[key] = source_row
                stats["taste_profiles_moved"] += 1
                continue

            timestamps = [
                value
                for value in (source_row.last_event_at, target_row.last_event_at)
                if value is not None
            ]
            latest_at = max(timestamps) if timestamps else None
            previous_target_event_at = target_row.last_event_at
            source_weight = float(source_row.raw_weight or 0)
            target_weight = float(target_row.raw_weight or 0)
            source_weight *= _decay_factor(
                source_row.last_event_at, latest_at
            )
            target_weight *= _decay_factor(
                target_row.last_event_at, latest_at
            )
            combined_weight = round(source_weight + target_weight, 4)
            target_row.raw_weight = Decimal(str(combined_weight))
            target_row.score = Decimal(
                str(_quantize(
                    _logistic(combined_weight)
                ))
            )
            target_row.event_count = int(target_row.event_count or 0) + int(
                source_row.event_count or 0
            )
            target_row.last_event_at = latest_at
            computed_at = [
                value
                for value in (source_row.last_computed_at, target_row.last_computed_at)
                if value is not None
            ]
            target_row.last_computed_at = max(computed_at) if computed_at else latest_at
            if source_row.last_event_at and (
                target_row.target_name is None
                or previous_target_event_at is None
                or source_row.last_event_at >= previous_target_event_at
            ):
                target_row.target_name = source_row.target_name or target_row.target_name
            await db.delete(source_row)
            stats["taste_profiles_merged"] += 1
        await db.flush()

        # 收藏有 (customer, product) 唯一约束：目标已有同菜品时去重，否则迁移。
        target_favorites = list(
            (
                await db.execute(
                    select(CustomerFavoriteProduct)
                    .where(CustomerFavoriteProduct.customer_id == int(target_id))
                    .order_by(CustomerFavoriteProduct.id)
                    .with_for_update()
                )
            ).scalars().all()
        )
        favorite_keys = {int(row.product_id) for row in target_favorites}
        source_favorites = list(
            (
                await db.execute(
                    select(CustomerFavoriteProduct)
                    .where(CustomerFavoriteProduct.customer_id == int(source_id))
                    .order_by(CustomerFavoriteProduct.id)
                    .with_for_update()
                )
            ).scalars().all()
        )
        for row in source_favorites:
            key = int(row.product_id)
            if key in favorite_keys:
                await db.delete(row)
                stats["favorites_deduplicated"] += 1
            else:
                row.customer_id = int(target_id)
                favorite_keys.add(key)
                stats["favorites_moved"] += 1
        await db.flush()

        # 生命周期是每顾客一条的夜批快照；保留计算时间较新的整份快照。
        lifecycle_rows = list(
            (
                await db.execute(
                    select(CustomerLifecycle)
                    .where(CustomerLifecycle.customer_id.in_((int(source_id), int(target_id))))
                    .order_by(CustomerLifecycle.id)
                    .with_for_update()
                )
            ).scalars().all()
        )
        source_lifecycle = next(
            (row for row in lifecycle_rows if int(row.customer_id) == int(source_id)), None
        )
        target_lifecycle = next(
            (row for row in lifecycle_rows if int(row.customer_id) == int(target_id)), None
        )
        if source_lifecycle is not None and target_lifecycle is None:
            source_lifecycle.customer_id = int(target_id)
            stats["lifecycle_rows_moved"] = 1
        elif source_lifecycle is not None and target_lifecycle is not None:
            if source_lifecycle.computed_at and (
                not target_lifecycle.computed_at
                or source_lifecycle.computed_at > target_lifecycle.computed_at
            ):
                for field in (
                    "lifecycle_code", "rfm_segment", "r_score", "f_score", "m_score",
                    "silence_days", "order_count_90d", "pay_amount_90d", "computed_at",
                ):
                    setattr(target_lifecycle, field, getattr(source_lifecycle, field))
            await db.delete(source_lifecycle)
            stats["lifecycle_rows_merged"] = 1
        await db.flush()

        # 订单发票申请和营销历史/待执行任务继续跟随合并后的当前顾客。
        await cls._reassign_simple(
            db, InvoiceRequest, source_id, target_id, "invoice_requests_moved", stats
        )
        await cls._reassign_simple(
            db, MarketingTask, source_id, target_id, "marketing_tasks_moved", stats
        )
        await cls._reassign_simple(
            db, MarketingMessageLog, source_id, target_id, "marketing_logs_moved", stats
        )
        await cls._reassign_simple(
            db, CampaignAttribution, source_id, target_id, "campaign_attributions_moved", stats
        )

        # 客群成员有 (segment, customer) 唯一约束；目标已在同一客群时去重。
        target_members = list(
            (
                await db.execute(
                    select(CustomerSegmentMember)
                    .where(CustomerSegmentMember.customer_id == int(target_id))
                    .order_by(CustomerSegmentMember.id)
                    .with_for_update()
                )
            ).scalars().all()
        )
        member_segment_ids = {int(row.segment_id) for row in target_members}
        source_members = list(
            (
                await db.execute(
                    select(CustomerSegmentMember)
                    .where(CustomerSegmentMember.customer_id == int(source_id))
                    .order_by(CustomerSegmentMember.id)
                    .with_for_update()
                )
            ).scalars().all()
        )
        for row in source_members:
            if int(row.segment_id) in member_segment_ids:
                await db.delete(row)
                stats["segment_members_deduplicated"] += 1
            else:
                row.customer_id = int(target_id)
                member_segment_ids.add(int(row.segment_id))
                stats["segment_members_moved"] += 1
        await db.flush()

    @classmethod
    async def _merge_table_party_memberships(
        cls, db: AsyncSession, source_id: int, target_id: int, stats: dict
    ) -> None:
        """将进行中的同桌局控制权、成员关系及共享草稿行归属迁到目标。"""
        host_party_ids = [
            int(row[0])
            for row in (
                await db.execute(
                    select(TableParty.id)
                    .where(TableParty.host_customer_id == int(source_id))
                    .order_by(TableParty.id)
                    .with_for_update()
                )
            ).all()
        ]
        host_result = await db.execute(
            update(TableParty)
            .where(TableParty.host_customer_id == int(source_id))
            .values(host_customer_id=int(target_id))
        )
        stats["table_party_hosts_moved"] += int(host_result.rowcount or 0)
        cart_result = await db.execute(
            update(SharedCartItem)
            .where(SharedCartItem.added_by_customer_id == int(source_id))
            .values(added_by_customer_id=int(target_id))
        )
        stats["shared_cart_items_moved"] += int(cart_result.rowcount or 0)

        target_members = list(
            (
                await db.execute(
                    select(TablePartyMember)
                    .where(TablePartyMember.customer_id == int(target_id))
                    .order_by(TablePartyMember.party_id, TablePartyMember.id)
                    .with_for_update()
                )
            ).scalars().all()
        )
        target_by_party = {int(row.party_id): row for row in target_members}
        target_party_ids = set(target_by_party)
        for party_id in host_party_ids:
            target_member = target_by_party.get(party_id)
            if target_member is None:
                target_member = TablePartyMember(
                    party_id=party_id,
                    customer_id=int(target_id),
                    role="HOST",
                    joined_at=now_local(),
                )
                db.add(target_member)
                target_by_party[party_id] = target_member
                target_party_ids.add(party_id)
            else:
                target_member.role = "HOST"
                target_member.left_at = None
        source_members = list(
            (
                await db.execute(
                    select(TablePartyMember)
                    .where(TablePartyMember.customer_id == int(source_id))
                    .order_by(TablePartyMember.party_id, TablePartyMember.id)
                    .with_for_update()
                )
            ).scalars().all()
        )
        for row in source_members:
            if int(row.party_id) in target_party_ids:
                # 目标已在同一桌局时保留源侧成员历史，但关闭其活动成员关系，
                # 避免违反 (party_id, customer_id) 唯一约束或双身份重复计数。
                if row.left_at is None:
                    row.left_at = now_local()
                    stats["table_party_members_closed"] += 1
            else:
                row.customer_id = int(target_id)
                target_party_ids.add(int(row.party_id))
                stats["table_party_members_moved"] += 1
        await db.flush()

    @classmethod
    async def _merge_faces(
        cls, db: AsyncSession, source_id: int, target_id: int, stats: dict
    ) -> None:
        await db.execute(
            update(FaceProfile)
            .where(FaceProfile.customer_id == source_id)
            .values(customer_id=target_id)
        )
        await db.flush()
        faces = list(
            (
                await db.execute(
                    select(FaceProfile)
                    .where(
                        FaceProfile.customer_id == target_id,
                        FaceProfile.consent_status == "AUTHORIZED",
                    )
                    .order_by(
                        FaceProfile.consent_at.desc(),
                        FaceProfile.id.desc(),
                    )
                )
            ).scalars().all()
        )
        if len(faces) <= 1:
            return
        keep = faces[0]
        from app.core.response import now_local

        now = now_local()
        for f in faces[1:]:
            if f.id == keep.id:
                continue
            f.consent_status = "REVOKED"
            f.revoked_at = now
            stats["faces_revoked"] += 1
        await db.flush()

    @classmethod
    async def _reassign_simple(
        cls,
        db: AsyncSession,
        model,
        source_id: int,
        target_id: int,
        stat_key: str,
        stats: dict,
    ) -> None:
        r = await db.execute(
            update(model).where(model.customer_id == source_id).values(customer_id=target_id)
        )
        stats[stat_key] = int(r.rowcount or 0)
        await db.flush()

    @classmethod
    async def _reassign_ledger(
        cls, db: AsyncSession, model, source_id: int, target_id: int,
        stat_key: str, stats: dict,
    ) -> None:
        """Move current ownership while preserving the account behind old snapshots."""
        result = await db.execute(
            update(model)
            .where(model.customer_id == int(source_id))
            .values(
                origin_customer_id=func.coalesce(model.origin_customer_id, model.customer_id),
                customer_id=int(target_id),
            )
        )
        stats[stat_key] = int(result.rowcount or 0)
        await db.flush()

    @classmethod
    async def _merge_wallet(
        cls, db: AsyncSession, source: Customer, target: Customer, stats: dict
    ) -> None:
        """合并储值余额与相关流水/充值单，避免源客禁用后余额滞留。"""
        dp = money(getattr(source, "balance_principal", 0) or 0)
        dg = money(getattr(source, "balance_gift", 0) or 0)
        if dp != 0 or dg != 0:
            target.balance_principal = money(getattr(target, "balance_principal", 0) or 0) + dp
            target.balance_gift = money(getattr(target, "balance_gift", 0) or 0) + dg
            source.balance_principal = money(0)
            source.balance_gift = money(0)
            stats["balance_principal_added"] = str(dp)
            stats["balance_gift_added"] = str(dg)
            db.add(
                BalanceLedger(
                    customer_id=target.id,
                    store_id=None,
                    entry_type="MERGE",
                    delta_principal=dp,
                    delta_gift=dg,
                    principal_after=money(target.balance_principal),
                    gift_after=money(target.balance_gift),
                    remark=f"合并自顾客#{source.id}",
                )
            )
        moved_bl = await db.execute(
            update(BalanceLedger)
            .where(BalanceLedger.customer_id == source.id)
            .values(
                origin_customer_id=func.coalesce(
                    BalanceLedger.origin_customer_id, BalanceLedger.customer_id
                ),
                customer_id=target.id,
            )
        )
        stats["balance_ledgers"] = int(moved_bl.rowcount or 0)
        moved_ro = await db.execute(
            update(RechargeOrder)
            .where(RechargeOrder.customer_id == source.id)
            .values(customer_id=target.id)
        )
        stats["recharge_orders"] = int(moved_ro.rowcount or 0)
        await db.flush()

    @classmethod
    async def _merge_social_benefits(
        cls, db: AsyncSession, source_id: int, target_id: int, stats: dict
    ) -> None:
        """合并公益身份认定与核销台账。"""
        target_rows = list(
            (
                await db.execute(
                    select(CustomerSocialIdentity)
                    .where(CustomerSocialIdentity.customer_id == int(target_id))
                    .with_for_update()
                )
            ).scalars().all()
        )
        target_active_codes = {
            str(r.identity_code or "").upper()
            for r in target_rows
            if str(r.status or "").upper() == "ACTIVE"
        }
        source_rows = list(
            (
                await db.execute(
                    select(CustomerSocialIdentity)
                    .where(CustomerSocialIdentity.customer_id == int(source_id))
                    .with_for_update()
                )
            ).scalars().all()
        )
        for row in source_rows:
            code = str(row.identity_code or "").upper()
            st = str(row.status or "").upper()
            if st in ("ACTIVE", "PENDING") and code in target_active_codes:
                row.status = "REVOKED"
                row.reject_reason = row.reject_reason or "合并账号：目标已有同身份生效"
                stats["social_identities_revoked"] += 1
            row.customer_id = int(target_id)
            stats["social_identities_moved"] += 1
            if st == "ACTIVE" and code not in target_active_codes:
                target_active_codes.add(code)
        await db.flush()

        moved = await db.execute(
            update(SocialBenefitRedemption)
            .where(SocialBenefitRedemption.customer_id == int(source_id))
            .values(customer_id=int(target_id))
        )
        stats["social_redemptions"] = int(moved.rowcount or 0)
        await db.flush()

    @classmethod
    async def _merge_points_and_meta(
        cls, db: AsyncSession, source: Customer, target: Customer, stats: dict
    ) -> None:
        add = int(source.points or 0)
        before = int(target.points or 0)
        target.points = before + add
        source.points = 0
        stats["points_added"] = add
        if add != 0:
            db.add(
                PointLedger(
                    customer_id=target.id,
                    order_id=None,
                    refund_id=None,
                    delta=add,
                    balance_after=int(target.points),
                    entry_type="MERGE",
                )
            )

        if source.last_order_at and (
            not target.last_order_at or source.last_order_at > target.last_order_at
        ):
            target.last_order_at = source.last_order_at

        CustomerService.fill_customer_display_if_empty(
            target,
            nickname=source.nickname,
            avatar_url=source.avatar_url,
        )
        if not target.phone and source.phone:
            target.phone = source.phone
            target.phone_verified = int(getattr(source, "phone_verified", 0) or 0)
        elif target.phone and source.phone and target.phone == source.phone:
            target.phone_verified = max(
                int(getattr(target, "phone_verified", 0) or 0),
                int(getattr(source, "phone_verified", 0) or 0),
            )
        if CustomerService.is_member(source) and not CustomerService.is_member(target):
            target.member_status = "MEMBER"
        activation_times = [
            value
            for value in (
                getattr(target, "member_activated_at", None),
                getattr(source, "member_activated_at", None),
            )
            if value is not None
        ]
        if activation_times:
            target.member_activated_at = min(activation_times)
        source_first_seen = getattr(source, "first_seen_at", None)
        target_first_seen = getattr(target, "first_seen_at", None)
        if source_first_seen and (not target_first_seen or source_first_seen < target_first_seen):
            target.first_seen_at = source_first_seen
            target.first_seen_store_id = getattr(source, "first_seen_store_id", None)
            target.first_seen_table_id = getattr(source, "first_seen_table_id", None)
        if not getattr(target, "registered_store_id", None) and getattr(
            source, "registered_store_id", None
        ):
            target.registered_store_id = source.registered_store_id
            target.registered_table_id = getattr(source, "registered_table_id", None)

        await db.flush()
        await CustomerService.recalculate_member_level(db, target)
        CustomerService.refresh_profile_status(target)
        await db.flush()

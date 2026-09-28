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
8. WelcomeLog / PointLedger 改挂目标；last_order_at 取两者较新。
9. 储值：本金/赠送金并入目标，写 MERGE 流水；balance_ledger / recharge_orders 改挂目标。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models import (
    BalanceLedger,
    Customer,
    CustomerAlipayAccount,
    CustomerCoupon,
    CustomerIdentity,
    CustomerPreference,
    CustomerWechatAccount,
    DiningSession,
    FaceProfile,
    Order,
    Payment,
    PointLedger,
    RechargeOrder,
    WelcomeLog,
)
from app.core.response import money
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
        "last_order_at": c.last_order_at.isoformat() if c.last_order_at else None,
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
            await db.execute(select(Customer).where(Customer.id.in_(ids)).with_for_update())
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
                    .where(Order.customer_id == source.id, Order.store_id != sid)
                    .limit(1)
                )
            ).scalar_one_or_none()
            foreign_recharge = (
                await db.execute(
                    select(RechargeOrder.id)
                    .where(RechargeOrder.customer_id == source.id, RechargeOrder.store_id != sid)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if foreign_order or foreign_recharge:
                raise AppException(40301, "源顾客在其他门店有订单或充值，请由总部账号合并")

        before = {
            "target": _customer_snapshot(target),
            "source": _customer_snapshot(source),
            "reason": (reason or "").strip() or None,
        }

        # P1-3 收集源顾客涉及的门店列表，写入审计日志，便于追溯跨门店数据合并
        source_store_ids = (
            await db.execute(
                select(Order.store_id).where(Order.customer_id == source.id).distinct()
            )
        ).scalars().all()
        source_store_ids = sorted({int(s) for s in source_store_ids if s is not None})

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
            "faces_revoked": 0,
            "welcome_logs": 0,
            "point_ledgers": 0,
            "points_added": 0,
            "balance_principal_added": "0.00",
            "balance_gift_added": "0.00",
            "balance_ledgers": 0,
            "recharge_orders": 0,
        }

        await cls._merge_platform_accounts(db, source.id, target.id, stats)
        await cls._merge_identities(db, source.id, target.id, stats)
        await cls._merge_dining_sessions(db, source.id, target.id, stats)
        await cls._reassign_orders_payments(db, source.id, target.id, stats)
        await cls._merge_coupons(db, source.id, target.id, stats)
        await cls._merge_preferences(db, source.id, target.id, stats)
        await cls._merge_faces(db, source.id, target.id, stats)
        await cls._reassign_simple(
            db, WelcomeLog, source.id, target.id, "welcome_logs", stats
        )
        await cls._reassign_simple(
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
            "cross_store": len(source_store_ids) > 1,
        }

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
            .values(customer_id=target.id)
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

"""社会贡献营销：身份认定、规则命中、限次、台账。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import money, now_local
from app.models import (
    Customer,
    CustomerSocialIdentity,
    SocialBenefitRedemption,
    SocialBenefitRule,
    SocialIdentityType,
)
from app.services.system_settings_service import SystemSettingsService


class SocialBenefitService:
    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    REDEEM_PENDING = "PENDING"
    REDEEM_NORMAL = "NORMAL"
    REDEEM_REVERSED = "REVERSED"

    @staticmethod
    async def is_enabled(db: AsyncSession, store_id: int | None = None) -> bool:
        # 优先读门店覆盖，再回落全局
        if store_id:
            try:
                from app.models import StoreSetting

                row = (
                    await db.execute(
                        select(StoreSetting).where(
                            StoreSetting.store_id == int(store_id),
                            StoreSetting.setting_key == "enable_social_benefit",
                        )
                    )
                ).scalar_one_or_none()
                if row is not None:
                    return str(row.setting_value).strip().lower() in ("1", "true", "yes", "on")
            except Exception:
                pass
        raw = await SystemSettingsService.get_value(db, "enable_social_benefit", "true")
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    async def list_identity_types(db: AsyncSession, *, include_disabled: bool = False) -> list[dict]:
        q = select(SocialIdentityType).order_by(SocialIdentityType.sort_order.asc(), SocialIdentityType.id.asc())
        if not include_disabled:
            q = q.where(SocialIdentityType.status == 1)
        rows = (await db.execute(q)).scalars().all()
        return [
            {
                "code": r.code,
                "name": r.name,
                "sort_order": r.sort_order,
                "status": r.status,
                "default_verify_hints": r.default_verify_hints or [],
            }
            for r in rows
        ]

    @staticmethod
    async def _get_type(db: AsyncSession, code: str) -> SocialIdentityType:
        row = (
            await db.execute(
                select(SocialIdentityType).where(
                    SocialIdentityType.code == code, SocialIdentityType.status == 1
                )
            )
        ).scalar_one_or_none()
        if not row:
            raise AppException(40002, f"无效公益身份: {code}")
        return row

    @staticmethod
    def _identity_payload(row: CustomerSocialIdentity, *, staff: bool = False) -> dict:
        data = {
            "id": row.id,
            "customer_id": row.customer_id,
            "identity_code": row.identity_code,
            "store_id": row.store_id,
            "status": row.status,
            "verify_method": row.verify_method,
            "credential_note": row.credential_note,
            "valid_from": row.valid_from.isoformat() if row.valid_from else None,
            "valid_to": row.valid_to.isoformat() if row.valid_to else None,
            "reject_reason": row.reject_reason,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        if staff:
            data["credential_media_url"] = row.credential_media_url
            data["reviewed_by"] = row.reviewed_by
            data["reviewed_at"] = row.reviewed_at.isoformat() if row.reviewed_at else None
        return data

    @staticmethod
    async def apply_identity(
        db: AsyncSession,
        *,
        customer_id: int,
        identity_code: str,
        store_id: int | None,
        verify_method: str = "MINI",
        credential_note: str | None = None,
        credential_media_url: str | None = None,
    ) -> CustomerSocialIdentity:
        await SocialBenefitService._get_type(db, identity_code)
        cust = await db.get(Customer, int(customer_id))
        if not cust or int(cust.status) != 1:
            raise AppException(40406, "顾客不存在或已停用")
        existing = (
            await db.execute(
                select(CustomerSocialIdentity).where(
                    CustomerSocialIdentity.customer_id == int(customer_id),
                    CustomerSocialIdentity.identity_code == identity_code,
                    CustomerSocialIdentity.status.in_(
                        (SocialBenefitService.PENDING, SocialBenefitService.ACTIVE)
                    ),
                )
            )
        ).scalar_one_or_none()
        if existing:
            if existing.status == SocialBenefitService.ACTIVE:
                raise AppException(40901, "该身份已认定生效")
            return existing
        row = CustomerSocialIdentity(
            customer_id=int(customer_id),
            identity_code=identity_code,
            store_id=int(store_id) if store_id else None,
            status=SocialBenefitService.PENDING,
            verify_method=verify_method,
            credential_note=(credential_note or "").strip()[:255] or None,
            credential_media_url=(credential_media_url or "").strip()[:500] or None,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def pos_verify(
        db: AsyncSession,
        *,
        customer_id: int,
        identity_code: str,
        store_id: int,
        operator_user_id: int,
        credential_note: str | None = None,
        activate: bool = True,
    ) -> CustomerSocialIdentity:
        """POS 当场核验：可直接 ACTIVE（P0 收银路径）。"""
        await SocialBenefitService._get_type(db, identity_code)
        cust = await db.get(Customer, int(customer_id))
        if not cust or int(cust.status) != 1:
            raise AppException(40406, "顾客不存在或已停用")
        now = now_local()
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)

        # 同身份其他 ACTIVE 置 REVOKED，保证单 ACTIVE
        actives = (
            await db.execute(
                select(CustomerSocialIdentity).where(
                    CustomerSocialIdentity.customer_id == int(customer_id),
                    CustomerSocialIdentity.identity_code == identity_code,
                    CustomerSocialIdentity.status == SocialBenefitService.ACTIVE,
                )
            )
        ).scalars().all()
        for a in actives:
            a.status = SocialBenefitService.REVOKED

        pending = (
            await db.execute(
                select(CustomerSocialIdentity).where(
                    CustomerSocialIdentity.customer_id == int(customer_id),
                    CustomerSocialIdentity.identity_code == identity_code,
                    CustomerSocialIdentity.status == SocialBenefitService.PENDING,
                )
            )
        ).scalar_one_or_none()
        if pending:
            row = pending
        else:
            row = CustomerSocialIdentity(
                customer_id=int(customer_id),
                identity_code=identity_code,
                store_id=int(store_id),
                verify_method="POS",
            )
            db.add(row)
        row.store_id = int(store_id)
        row.verify_method = "POS"
        row.credential_note = (credential_note or "").strip()[:255] or "POS当场核验"
        if activate:
            row.status = SocialBenefitService.ACTIVE
            row.reviewed_by = int(operator_user_id)
            row.reviewed_at = now
            row.valid_from = now
            row.reject_reason = None
        else:
            row.status = SocialBenefitService.PENDING
        await db.flush()
        return row

    @staticmethod
    async def review_identity(
        db: AsyncSession,
        *,
        identity_id: int,
        approve: bool,
        reviewer_id: int,
        reject_reason: str | None = None,
        store_id: int | None = None,
    ) -> CustomerSocialIdentity:
        row = await db.get(CustomerSocialIdentity, int(identity_id))
        if not row:
            raise AppException(40406, "认定记录不存在")
        if store_id is not None and row.store_id and int(row.store_id) != int(store_id):
            raise AppException(40301, "无权审核其他门店认定")
        if row.status not in (SocialBenefitService.PENDING, SocialBenefitService.ACTIVE):
            raise AppException(40008, f"当前状态不可审核: {row.status}")
        now = now_local()
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        row.reviewed_by = int(reviewer_id)
        row.reviewed_at = now
        if approve:
            # 撤销同身份其他 ACTIVE
            others = (
                await db.execute(
                    select(CustomerSocialIdentity).where(
                        CustomerSocialIdentity.customer_id == row.customer_id,
                        CustomerSocialIdentity.identity_code == row.identity_code,
                        CustomerSocialIdentity.status == SocialBenefitService.ACTIVE,
                        CustomerSocialIdentity.id != row.id,
                    )
                )
            ).scalars().all()
            for o in others:
                o.status = SocialBenefitService.REVOKED
            row.status = SocialBenefitService.ACTIVE
            row.valid_from = row.valid_from or now
            row.reject_reason = None
        else:
            row.status = SocialBenefitService.REJECTED
            row.reject_reason = (reject_reason or "审核未通过").strip()[:255]
        await db.flush()
        return row

    @staticmethod
    async def list_identities(
        db: AsyncSession,
        *,
        store_id: int | None = None,
        status: str | None = None,
        customer_id: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        q = select(CustomerSocialIdentity)
        cq = select(func.count()).select_from(CustomerSocialIdentity)
        if store_id is not None:
            cond = or_(
                CustomerSocialIdentity.store_id == int(store_id),
                CustomerSocialIdentity.store_id.is_(None),
            )
            q = q.where(cond)
            cq = cq.where(cond)
        if status:
            q = q.where(CustomerSocialIdentity.status == status)
            cq = cq.where(CustomerSocialIdentity.status == status)
        if customer_id:
            q = q.where(CustomerSocialIdentity.customer_id == int(customer_id))
            cq = cq.where(CustomerSocialIdentity.customer_id == int(customer_id))
        total = int((await db.execute(cq)).scalar() or 0)
        page = max(1, page)
        page_size = min(100, max(1, page_size))
        rows = (
            await db.execute(
                q.order_by(CustomerSocialIdentity.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
        return [SocialBenefitService._identity_payload(r, staff=True) for r in rows], total

    @staticmethod
    async def mine_identities(db: AsyncSession, customer_id: int) -> list[dict]:
        rows = (
            await db.execute(
                select(CustomerSocialIdentity)
                .where(CustomerSocialIdentity.customer_id == int(customer_id))
                .order_by(CustomerSocialIdentity.id.desc())
            )
        ).scalars().all()
        return [SocialBenefitService._identity_payload(r, staff=False) for r in rows]

    @staticmethod
    async def active_codes(db: AsyncSession, customer_id: int) -> list[str]:
        now = now_local()
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        rows = (
            await db.execute(
                select(CustomerSocialIdentity).where(
                    CustomerSocialIdentity.customer_id == int(customer_id),
                    CustomerSocialIdentity.status == SocialBenefitService.ACTIVE,
                )
            )
        ).scalars().all()
        codes: list[str] = []
        for r in rows:
            if r.valid_to and r.valid_to.replace(tzinfo=None) < now:
                r.status = SocialBenefitService.EXPIRED
                continue
            if r.valid_from and r.valid_from.replace(tzinfo=None) > now:
                continue
            codes.append(r.identity_code)
        return codes

    @staticmethod
    def _in_time_windows(windows: list | None, now_t: time) -> bool:
        if not windows:
            return True
        for w in windows:
            if not isinstance(w, (list, tuple)) or len(w) < 2:
                continue
            try:
                start = time.fromisoformat(str(w[0]))
                end = time.fromisoformat(str(w[1]))
            except ValueError:
                continue
            if start <= end:
                if start <= now_t <= end:
                    return True
            else:
                # 跨午夜
                if now_t >= start or now_t <= end:
                    return True
        return False

    @staticmethod
    def _weekday_ok(weekdays: list | None, weekday: int) -> bool:
        if not weekdays:
            return True
        try:
            return int(weekday) in {int(x) for x in weekdays}
        except (TypeError, ValueError):
            return True

    @staticmethod
    async def _today_redeem_count(
        db: AsyncSession, *, customer_id: int, store_id: int, day: date
    ) -> int:
        start = datetime.combine(day, time.min)
        end = start + timedelta(days=1)
        cnt = (
            await db.execute(
                select(func.count())
                .select_from(SocialBenefitRedemption)
                .where(
                    SocialBenefitRedemption.customer_id == int(customer_id),
                    SocialBenefitRedemption.store_id == int(store_id),
                    SocialBenefitRedemption.status.in_(
                        (SocialBenefitService.REDEEM_PENDING, SocialBenefitService.REDEEM_NORMAL)
                    ),
                    SocialBenefitRedemption.created_at >= start,
                    SocialBenefitRedemption.created_at < end,
                )
            )
        ).scalar()
        return int(cnt or 0)

    @staticmethod
    async def find_best_rule(
        db: AsyncSession,
        *,
        customer_id: int,
        store_id: int,
        product_ids: list[int] | None = None,
        category_ids: list[int] | None = None,
        at: datetime | None = None,
    ) -> tuple[SocialBenefitRule, str] | None:
        if not await SocialBenefitService.is_enabled(db, store_id):
            return None
        codes = await SocialBenefitService.active_codes(db, customer_id)
        if not codes:
            return None
        now = at or now_local()
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        day = now.date()
        weekday = day.isoweekday()  # 1=Mon..7=Sun
        now_t = now.time().replace(microsecond=0)

        rules = (
            await db.execute(
                select(SocialBenefitRule)
                .where(
                    SocialBenefitRule.status == 1,
                    SocialBenefitRule.identity_code.in_(codes),
                    or_(
                        SocialBenefitRule.store_id.is_(None),
                        SocialBenefitRule.store_id == int(store_id),
                    ),
                    SocialBenefitRule.benefit_type == "ORDER_DISCOUNT",
                )
                .order_by(
                    SocialBenefitRule.store_id.is_(None).asc(),  # 门店规则优先于总部
                    SocialBenefitRule.priority.asc(),
                    SocialBenefitRule.id.asc(),
                )
            )
        ).scalars().all()

        used = await SocialBenefitService._today_redeem_count(
            db, customer_id=customer_id, store_id=store_id, day=day
        )
        product_ids = [int(x) for x in (product_ids or [])]
        category_ids = [int(x) for x in (category_ids or [])]

        for rule in rules:
            if rule.date_from and day < rule.date_from:
                continue
            if rule.date_to and day > rule.date_to:
                continue
            if not SocialBenefitService._weekday_ok(rule.weekdays, weekday):
                continue
            if not SocialBenefitService._in_time_windows(rule.time_windows, now_t):
                continue
            if rule.daily_limit is not None and used >= int(rule.daily_limit):
                continue
            app_prods = rule.applicable_product_ids
            app_cats = rule.applicable_category_ids
            if app_prods and product_ids:
                if not set(int(x) for x in app_prods) & set(product_ids):
                    continue
            if app_cats and category_ids:
                if not set(int(x) for x in app_cats) & set(category_ids):
                    continue
            if rule.discount_rate is None:
                continue
            rate = money(rule.discount_rate)
            if rate <= 0 or rate >= 1:
                continue
            return rule, rule.identity_code
        return None

    @staticmethod
    def calc_social_discount(
        *,
        discountable_subtotal: Decimal,
        packing_fee: Decimal,
        rate: Decimal,
        max_subsidy: Decimal | None = None,
    ) -> tuple[Decimal, Decimal]:
        """返回 (subsidy, total_payable)。补贴只作用在可打折小计。"""
        base = money(discountable_subtotal)
        rate = money(rate)
        subsidy = money(base * (Decimal("1") - rate))
        if max_subsidy is not None:
            subsidy = min(subsidy, money(max_subsidy))
        total = money(base - subsidy + money(packing_fee) + money(0))
        # packing already separate; caller passes subtotal split — recalculate with full subtotal outside
        return subsidy, total

    @staticmethod
    async def preview(
        db: AsyncSession,
        *,
        customer_id: int,
        store_id: int,
        subtotal: Decimal,
        discountable_subtotal: Decimal,
        packing_fee: Decimal = Decimal("0"),
        product_ids: list[int] | None = None,
        category_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        hit = await SocialBenefitService.find_best_rule(
            db,
            customer_id=customer_id,
            store_id=store_id,
            product_ids=product_ids,
            category_ids=category_ids,
        )
        if not hit:
            return {"eligible": False, "reason": "无可用公益权益"}
        rule, identity_code = hit
        rate = money(rule.discount_rate)
        # 补贴仅作用于可打折部分；特价/不可打折商品与打包费原价
        special = money(money(subtotal) - money(discountable_subtotal))
        if special < 0:
            special = money(0)
        subsidy = money(money(discountable_subtotal) * (Decimal("1") - rate))
        if rule.max_subsidy_amount is not None:
            subsidy = min(subsidy, money(rule.max_subsidy_amount))
        total = money(money(discountable_subtotal) - subsidy + special + money(packing_fee))
        return {
            "eligible": True,
            "identity_code": identity_code,
            "rule_id": rule.id,
            "rule_name": rule.name,
            "benefit_type": rule.benefit_type,
            "discount_rate": str(rate),
            "stack_policy": rule.stack_policy or "EXCLUSIVE",
            "subsidy_amount": str(subsidy),
            "total_amount": str(total),
            "daily_limit": rule.daily_limit,
        }

    @staticmethod
    async def reserve_for_order(
        db: AsyncSession,
        *,
        order_id: int,
        customer_id: int,
        store_id: int,
        rule: SocialBenefitRule,
        identity_code: str,
        subsidy_amount: Decimal,
        operator_user_id: int | None = None,
        meta: dict | None = None,
    ) -> SocialBenefitRedemption:
        key = f"sbr:order:{int(order_id)}"
        existing = (
            await db.execute(
                select(SocialBenefitRedemption).where(SocialBenefitRedemption.idempotency_key == key)
            )
        ).scalar_one_or_none()
        if existing:
            # 绑客/加菜重算：REVERSED 行可复活；已 PENDING/NORMAL 则刷新补贴金额
            if existing.status in (
                SocialBenefitService.REDEEM_REVERSED,
                SocialBenefitService.REDEEM_PENDING,
            ):
                existing.customer_id = int(customer_id)
                existing.store_id = int(store_id)
                existing.identity_code = identity_code
                existing.rule_id = int(rule.id)
                existing.benefit_type = rule.benefit_type
                existing.subsidy_amount = money(subsidy_amount)
                existing.operator_user_id = int(operator_user_id) if operator_user_id else None
                existing.meta = meta or {}
                existing.status = SocialBenefitService.REDEEM_PENDING
                existing.redeemed_at = None
                await db.flush()
            return existing
        # 再验限次（防并发）
        day = now_local().date()
        used = await SocialBenefitService._today_redeem_count(
            db, customer_id=customer_id, store_id=store_id, day=day
        )
        if rule.daily_limit is not None and used >= int(rule.daily_limit):
            raise AppException(40008, "今日公益权益次数已用完")
        row = SocialBenefitRedemption(
            customer_id=int(customer_id),
            store_id=int(store_id),
            order_id=int(order_id),
            identity_code=identity_code,
            rule_id=int(rule.id),
            benefit_type=rule.benefit_type,
            subsidy_amount=money(subsidy_amount),
            operator_user_id=int(operator_user_id) if operator_user_id else None,
            idempotency_key=key,
            status=SocialBenefitService.REDEEM_PENDING,
            meta=meta or {},
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def confirm_paid(db: AsyncSession, order_id: int) -> None:
        rows = (
            await db.execute(
                select(SocialBenefitRedemption).where(
                    SocialBenefitRedemption.order_id == int(order_id),
                    SocialBenefitRedemption.status == SocialBenefitService.REDEEM_PENDING,
                )
            )
        ).scalars().all()
        now = now_local()
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        for r in rows:
            r.status = SocialBenefitService.REDEEM_NORMAL
            r.redeemed_at = now
        if rows:
            await db.flush()

    @staticmethod
    async def reverse_for_order(db: AsyncSession, order_id: int) -> None:
        rows = (
            await db.execute(
                select(SocialBenefitRedemption).where(
                    SocialBenefitRedemption.order_id == int(order_id),
                    SocialBenefitRedemption.status.in_(
                        (SocialBenefitService.REDEEM_PENDING, SocialBenefitService.REDEEM_NORMAL)
                    ),
                )
            )
        ).scalars().all()
        for r in rows:
            r.status = SocialBenefitService.REDEEM_REVERSED
        if rows:
            await db.flush()

    @staticmethod
    async def list_redemptions(
        db: AsyncSession,
        *,
        store_id: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        q = select(SocialBenefitRedemption)
        cq = select(func.count()).select_from(SocialBenefitRedemption)
        if store_id is not None:
            q = q.where(SocialBenefitRedemption.store_id == int(store_id))
            cq = cq.where(SocialBenefitRedemption.store_id == int(store_id))
        total = int((await db.execute(cq)).scalar() or 0)
        page = max(1, page)
        page_size = min(100, max(1, page_size))
        rows = (
            await db.execute(
                q.order_by(SocialBenefitRedemption.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
        items = [
            {
                "id": r.id,
                "customer_id": r.customer_id,
                "store_id": r.store_id,
                "order_id": r.order_id,
                "identity_code": r.identity_code,
                "rule_id": r.rule_id,
                "benefit_type": r.benefit_type,
                "subsidy_amount": str(r.subsidy_amount),
                "status": r.status,
                "redeemed_at": r.redeemed_at.isoformat() if r.redeemed_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
        return items, total

    @staticmethod
    async def monthly_stats(
        db: AsyncSession,
        *,
        store_id: int | None = None,
        year: int | None = None,
        month: int | None = None,
    ) -> dict[str, Any]:
        """公益看板：本月人次、补贴合计、身份分布。"""
        now = now_local()
        y = int(year or now.year)
        m = int(month or now.month)
        start = datetime(y, m, 1)
        if m == 12:
            end = datetime(y + 1, 1, 1)
        else:
            end = datetime(y, m + 1, 1)
        q = select(
            SocialBenefitRedemption.identity_code,
            func.count(SocialBenefitRedemption.id),
            func.coalesce(func.sum(SocialBenefitRedemption.subsidy_amount), 0),
        ).where(
            SocialBenefitRedemption.status == SocialBenefitService.REDEEM_NORMAL,
            SocialBenefitRedemption.redeemed_at >= start,
            SocialBenefitRedemption.redeemed_at < end,
        )
        if store_id is not None:
            q = q.where(SocialBenefitRedemption.store_id == int(store_id))
        q = q.group_by(SocialBenefitRedemption.identity_code)
        by_identity = []
        total_cnt = 0
        total_sub = Decimal("0.00")
        for code, cnt, sub in (await db.execute(q)).all():
            c = int(cnt or 0)
            s = money(Decimal(str(sub or 0)))
            total_cnt += c
            total_sub += s
            by_identity.append(
                {"identity_code": code, "redemptions": c, "subsidy_amount": str(s)}
            )
        pending = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(CustomerSocialIdentity)
                    .where(CustomerSocialIdentity.status == SocialBenefitService.PENDING)
                )
            ).scalar()
            or 0
        )
        return {
            "year": y,
            "month": m,
            "redemptions": total_cnt,
            "subsidy_amount": str(money(total_sub)),
            "by_identity": by_identity,
            "pending_reviews": pending,
        }

    @staticmethod
    async def run_love_day_issue(db: AsyncSession) -> dict[str, Any]:
        """爱心日：按模板名给 ACTIVE 公益身份顾客发券（幂等 love_day:日期:cid:tid）。"""
        from app.models import CouponTemplate
        from app.services.coupon_service import CouponService

        if not await SocialBenefitService.is_enabled(db):
            return {"skipped": "disabled"}
        weekday = await SystemSettingsService.get_int(db, "love_day_weekday", 0)
        today = now_local().date()
        if int(today.weekday()) != int(weekday):
            return {"skipped": "not_love_day", "weekday": weekday, "today": today.isoformat()}
        name = (
            await SystemSettingsService.get_value(db, "love_day_soy_template_name", "")
        ).strip()
        if not name:
            return {"skipped": "no_template"}
        tmpl = (
            await db.execute(
                select(CouponTemplate).where(
                    CouponTemplate.name == name,
                    CouponTemplate.status == 1,
                )
            )
        ).scalar_one_or_none()
        if not tmpl:
            return {"skipped": "template_missing", "name": name}
        identities = list(
            (
                await db.execute(
                    select(CustomerSocialIdentity.customer_id)
                    .where(CustomerSocialIdentity.status == SocialBenefitService.ACTIVE)
                    .distinct()
                )
            ).scalars().all()
        )
        issued = 0
        skipped = 0
        errors = 0
        day_key = today.isoformat()
        for cid in identities:
            key = f"love_day:{day_key}:{int(cid)}:{int(tmpl.id)}"
            try:
                rows = await CouponService.issue_by_template_id(
                    db,
                    int(cid),
                    int(tmpl.id),
                    quantity=1,
                    client_request_id=key,
                )
                if rows:
                    issued += 1
                else:
                    skipped += 1
            except AppException:
                skipped += 1
            except Exception:
                errors += 1
        return {
            "date": day_key,
            "template_id": int(tmpl.id),
            "targets": len(identities),
            "issued": issued,
            "skipped": skipped,
            "errors": errors,
        }

    @staticmethod
    async def list_rules(db: AsyncSession, *, store_id: int | None = None) -> list[dict]:
        q = select(SocialBenefitRule).where(SocialBenefitRule.status == 1)
        if store_id is not None:
            q = q.where(
                or_(SocialBenefitRule.store_id.is_(None), SocialBenefitRule.store_id == int(store_id))
            )
        rows = (
            await db.execute(q.order_by(SocialBenefitRule.priority.asc(), SocialBenefitRule.id.asc()))
        ).scalars().all()
        return [
            {
                "id": r.id,
                "store_id": r.store_id,
                "identity_code": r.identity_code,
                "name": r.name,
                "benefit_type": r.benefit_type,
                "discount_rate": str(r.discount_rate) if r.discount_rate is not None else None,
                "daily_limit": r.daily_limit,
                "stack_policy": r.stack_policy,
                "priority": r.priority,
                "status": r.status,
            }
            for r in rows
        ]

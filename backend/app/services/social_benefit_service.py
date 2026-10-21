"""社会贡献营销：身份认定、规则命中、限次、台账。"""
from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


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
    def _is_staff_reapply_ban(reject_reason: str | None) -> bool:
        """店员主动取消才禁止小程序再申请；合并/换证作废不封禁。"""
        r = (reject_reason or "").strip()
        if not r:
            return False
        return (
            r.startswith("店员取消")
            or "顾客管理取消" in r
            or r.startswith("禁止再申请")
        )

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
            raise AppException(
                40002,
                f"无效公益身份: {code}（库中无启用的身份类型，请导入 sql/data_sys.sql 中的 social_identity_types，或到「社会贡献」确认身份配置）",
            )
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
        if not await SocialBenefitService.is_enabled(db, store_id=store_id):
            raise AppException(40008, "本店暂未开启社会贡献公益，请联系店员")
        await SocialBenefitService._get_type(db, identity_code)
        cust = await db.get(Customer, int(customer_id))
        if not cust or int(cust.status) != 1:
            raise AppException(40406, "顾客不存在或已停用")
        # 仅「店员主动取消」永久禁申；合并冲突 / 换证作废产生的 REVOKED 不封禁
        revoked_rows = (
            await db.execute(
                select(CustomerSocialIdentity.reject_reason).where(
                    CustomerSocialIdentity.customer_id == int(customer_id),
                    CustomerSocialIdentity.status == SocialBenefitService.REVOKED,
                )
            )
        ).scalars().all()
        if any(SocialBenefitService._is_staff_reapply_ban(r) for r in revoked_rows):
            raise AppException(40901, "您的公益身份已被取消，不可再次申请，如有疑问请到店咨询")
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
        # 拒绝后再申请：复用同顾客同身份最新 REJECTED 记录，避免堆多条且便于审核追溯
        rejected = (
            await db.execute(
                select(CustomerSocialIdentity)
                .where(
                    CustomerSocialIdentity.customer_id == int(customer_id),
                    CustomerSocialIdentity.identity_code == identity_code,
                    CustomerSocialIdentity.status == SocialBenefitService.REJECTED,
                )
                .order_by(CustomerSocialIdentity.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if rejected:
            row = rejected
            row.store_id = int(store_id) if store_id else None
            row.status = SocialBenefitService.PENDING
            row.verify_method = verify_method
            row.credential_note = (credential_note or "").strip()[:255] or None
            row.credential_media_url = (credential_media_url or "").strip()[:500] or None
            row.reject_reason = None
            row.reviewed_by = None
            row.reviewed_at = None
            row.valid_from = None
            row.valid_to = None
        else:
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
        # server_default 的 created_at/updated_at 须 refresh，否则 commit 后读属性会 MissingGreenlet
        await db.refresh(row)
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
            a.reject_reason = "换证作废：同身份已重新认定"

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
                o.reject_reason = "换证作废：同身份已重新认定"
            row.status = SocialBenefitService.ACTIVE
            row.valid_from = row.valid_from or now
            row.reject_reason = None
        else:
            row.status = SocialBenefitService.REJECTED
            row.reject_reason = (reject_reason or "审核未通过").strip()[:255]
        await db.flush()
        return row

    @staticmethod
    async def revoke_identity(
        db: AsyncSession,
        *,
        identity_id: int,
        operator_id: int,
        store_id: int | None = None,
        reason: str | None = None,
    ) -> CustomerSocialIdentity:
        """取消公益身份：ACTIVE/PENDING 均→REVOKED（取消后不可再小程序申请）。"""
        row = await db.get(CustomerSocialIdentity, int(identity_id))
        if not row:
            raise AppException(40406, "认定记录不存在")
        if store_id is not None and row.store_id and int(row.store_id) != int(store_id):
            raise AppException(40301, "无权取消其他门店认定")
        if row.status not in (SocialBenefitService.PENDING, SocialBenefitService.ACTIVE):
            raise AppException(40008, f"当前状态不可取消: {row.status}")
        now = now_local()
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        note = (reason or "").strip()[:255]
        row.reviewed_by = int(operator_id)
        row.reviewed_at = now
        was_pending = row.status == SocialBenefitService.PENDING
        row.status = SocialBenefitService.REVOKED
        row.reject_reason = note or ("店员取消申请" if was_pending else "店员取消公益身份")
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
        items = [SocialBenefitService._identity_payload(r, staff=True) for r in rows]
        # 待审列表补姓名/手机，方便店员核对（不改表结构）
        cids = sorted({int(r.customer_id) for r in rows if r.customer_id})
        if cids:
            custs = (
                await db.execute(select(Customer).where(Customer.id.in_(cids)))
            ).scalars().all()
            cmap = {int(c.id): c for c in custs}
            for it in items:
                c = cmap.get(int(it["customer_id"]))
                if not c:
                    continue
                from app.services.customer_service import CustomerService

                it.update(CustomerService.staff_customer_name_fields(c))
                it["customer_phone"] = c.phone
                it["customer_no"] = getattr(c, "customer_no", None)
        await SocialBenefitService._enrich_identity_type_names(db, items)
        return items, total

    @staticmethod
    async def _enrich_identity_type_names(db: AsyncSession, items: list[dict]) -> None:
        codes = sorted({str(it.get("identity_code") or "") for it in items if it.get("identity_code")})
        if not codes:
            return
        rows = (
            await db.execute(
                select(SocialIdentityType.code, SocialIdentityType.name).where(
                    SocialIdentityType.code.in_(codes)
                )
            )
        ).all()
        name_map = {str(code): name for code, name in rows}
        for it in items:
            code = str(it.get("identity_code") or "")
            it["identity_name"] = name_map.get(code) or code

    @staticmethod
    async def pending_review_summary(
        db: AsyncSession,
        *,
        store_id: int | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """数据概览待办：待审公益身份数量 + 最近几条。"""
        limit = min(20, max(1, int(limit)))
        base = select(CustomerSocialIdentity).where(
            CustomerSocialIdentity.status == SocialBenefitService.PENDING
        )
        count_q = select(func.count()).select_from(CustomerSocialIdentity).where(
            CustomerSocialIdentity.status == SocialBenefitService.PENDING
        )
        if store_id is not None:
            cond = or_(
                CustomerSocialIdentity.store_id == int(store_id),
                CustomerSocialIdentity.store_id.is_(None),
            )
            base = base.where(cond)
            count_q = count_q.where(cond)
        total = int((await db.execute(count_q)).scalar() or 0)
        rows = list(
            (
                await db.execute(
                    base.order_by(CustomerSocialIdentity.id.desc()).limit(limit)
                )
            ).scalars().all()
        )
        items = [SocialBenefitService._identity_payload(r, staff=True) for r in rows]
        cids = sorted({int(r.customer_id) for r in rows if r.customer_id})
        if cids:
            custs = (
                await db.execute(select(Customer).where(Customer.id.in_(cids)))
            ).scalars().all()
            cmap = {int(c.id): c for c in custs}
            for it in items:
                c = cmap.get(int(it["customer_id"]))
                if not c:
                    continue
                from app.services.customer_service import CustomerService

                it.update(CustomerService.staff_customer_name_fields(c))
                it["customer_phone"] = c.phone
                it["customer_no"] = getattr(c, "customer_no", None)
        await SocialBenefitService._enrich_identity_type_names(db, items)
        return {"pending_count": total, "items": items}

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

    _WEEKDAY_ZH = ("", "周一", "周二", "周三", "周四", "周五", "周六", "周日")

    @staticmethod
    def _fmt_rule_label(rule: SocialBenefitRule) -> str:
        name = (getattr(rule, "name", None) or "").strip()
        return f"「{name}」" if name else f"#{getattr(rule, 'id', '?')}"

    @staticmethod
    def _fmt_time_windows(windows: list | None) -> str:
        parts: list[str] = []
        for w in windows or []:
            if isinstance(w, (list, tuple)) and len(w) >= 2:
                parts.append(f"{w[0]}-{w[1]}")
        return "、".join(parts)

    @staticmethod
    def _fmt_weekdays(weekdays: list | None) -> str:
        labels: list[str] = []
        for x in weekdays or []:
            try:
                i = int(x)
            except (TypeError, ValueError):
                continue
            if 1 <= i <= 7:
                labels.append(SocialBenefitService._WEEKDAY_ZH[i])
        return "、".join(labels) if labels else "指定星期"

    @staticmethod
    def _rule_reject(
        rule: SocialBenefitRule,
        *,
        day: date,
        weekday: int,
        now_t: time,
        used: int,
        product_ids: list[int],
        category_ids: list[int],
    ) -> tuple[int, str] | None:
        """规则不可用时返回 (优先级, 文案)，数字越小越优先展示；可用则返回 None。"""
        label = SocialBenefitService._fmt_rule_label(rule)
        if rule.date_from and day < rule.date_from:
            return (
                50,
                f"公益规则{label}尚未生效（生效日 {rule.date_from.isoformat()}）",
            )
        if rule.date_to and day > rule.date_to:
            return (
                50,
                f"公益规则{label}已过期（截止日期 {rule.date_to.isoformat()}）",
            )
        if not SocialBenefitService._weekday_ok(rule.weekdays, weekday):
            today = SocialBenefitService._WEEKDAY_ZH[weekday]
            avail = SocialBenefitService._fmt_weekdays(rule.weekdays)
            return (40, f"今日是{today}，公益规则{label}仅限{avail}可用")
        if not SocialBenefitService._in_time_windows(rule.time_windows, now_t):
            tw = SocialBenefitService._fmt_time_windows(rule.time_windows)
            now_s = now_t.strftime("%H:%M")
            detail = tw or "已配置时段"
            return (30, f"当前时间 {now_s} 不在公益可用时段（规则{label}：{detail}）")
        if rule.daily_limit is not None and used >= int(rule.daily_limit):
            return (
                10,
                f"今日公益折扣次数已用完（规则{label}每日限 {int(rule.daily_limit)} 次，"
                f"本店今日已用 {used} 次）",
            )
        app_prods = rule.applicable_product_ids
        app_cats = rule.applicable_category_ids
        if app_prods and product_ids:
            if not set(int(x) for x in app_prods) & set(product_ids):
                return (20, f"当前购物车餐品不适用公益折扣（规则{label}仅限指定餐品）")
        if app_cats and category_ids:
            if not set(int(x) for x in app_cats) & set(category_ids):
                return (20, f"当前购物车餐品不适用公益折扣（规则{label}仅限指定分类）")
        if rule.discount_rate is None:
            return (60, f"公益规则{label}折扣率未配置，请联系店长检查")
        rate = money(rule.discount_rate)
        if rate <= 0 or rate >= 1:
            return (60, f"公益规则{label}折扣率配置无效，请联系店长检查")
        return None

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
    async def _candidate_rules(
        db: AsyncSession, *, codes: list[str], store_id: int
    ) -> list[SocialBenefitRule]:
        if not codes:
            return []
        return list(
            (
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
        )

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

        rules = await SocialBenefitService._candidate_rules(
            db, codes=codes, store_id=store_id
        )
        used = await SocialBenefitService._today_redeem_count(
            db, customer_id=customer_id, store_id=store_id, day=day
        )
        product_ids = [int(x) for x in (product_ids or [])]
        category_ids = [int(x) for x in (category_ids or [])]

        for rule in rules:
            if SocialBenefitService._rule_reject(
                rule,
                day=day,
                weekday=weekday,
                now_t=now_t,
                used=used,
                product_ids=product_ids,
                category_ids=category_ids,
            ) is None:
                return rule, rule.identity_code
        return None

    @staticmethod
    async def explain_ineligible(
        db: AsyncSession,
        *,
        customer_id: int,
        store_id: int,
        product_ids: list[int] | None = None,
        category_ids: list[int] | None = None,
        at: datetime | None = None,
    ) -> str:
        """收银可读：为何当前无法享用公益折扣（保证返回具体原因）。"""
        if not await SocialBenefitService.is_enabled(db, store_id):
            return "本店暂未开启社会贡献公益"
        codes = await SocialBenefitService.active_codes(db, customer_id)
        if not codes:
            return "该会员尚无已生效的公益身份，请先在收银台「当场核验」或完成小程序申请审核"
        now = at or now_local()
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        day = now.date()
        weekday = day.isoweekday()
        now_t = now.time().replace(microsecond=0)
        rules = await SocialBenefitService._candidate_rules(
            db, codes=codes, store_id=store_id
        )
        if not rules:
            codes_txt = "、".join(codes)
            return f"当前公益身份（{codes_txt}）暂无启用的折扣规则，请联系店长在「社会贡献」中配置"
        used = await SocialBenefitService._today_redeem_count(
            db, customer_id=customer_id, store_id=store_id, day=day
        )
        pids = [int(x) for x in (product_ids or [])]
        cids = [int(x) for x in (category_ids or [])]
        best: tuple[int, str] | None = None
        for rule in rules:
            reject = SocialBenefitService._rule_reject(
                rule,
                day=day,
                weekday=weekday,
                now_t=now_t,
                used=used,
                product_ids=pids,
                category_ids=cids,
            )
            if reject is None:
                # 与 find_best_rule 竞态/不一致时的兜底
                return "当前暂无法享用公益折扣，请稍后重试或联系店长"
            if best is None or reject[0] < best[0]:
                best = reject
        return best[1] if best else "当前暂无法享用公益折扣"

    @staticmethod
    async def require_best_rule(
        db: AsyncSession,
        *,
        customer_id: int,
        store_id: int,
        product_ids: list[int] | None = None,
        category_ids: list[int] | None = None,
        at: datetime | None = None,
    ) -> tuple[SocialBenefitRule, str]:
        """显式勾选公益折扣时：必须命中规则，否则抛清晰错误（禁止静默回落会员折）。"""
        hit = await SocialBenefitService.find_best_rule(
            db,
            customer_id=customer_id,
            store_id=store_id,
            product_ids=product_ids,
            category_ids=category_ids,
            at=at,
        )
        if hit:
            return hit
        msg = await SocialBenefitService.explain_ineligible(
            db,
            customer_id=customer_id,
            store_id=store_id,
            product_ids=product_ids,
            category_ids=category_ids,
            at=at,
        )
        raise AppException(40008, msg)

    @staticmethod
    def _empty_social_snapshot() -> dict[str, Any]:
        return {
            "social_applied": False,
            "social_identity_code": None,
            "social_discount": "0.00",
        }

    @staticmethod
    async def order_social_snapshot(db: AsyncSession, order_id: int) -> dict[str, Any]:
        """订单是否已挂公益台账（PENDING/NORMAL）。"""
        row = (
            await db.execute(
                select(SocialBenefitRedemption)
                .where(
                    SocialBenefitRedemption.order_id == int(order_id),
                    SocialBenefitRedemption.status.in_(
                        (
                            SocialBenefitService.REDEEM_PENDING,
                            SocialBenefitService.REDEEM_NORMAL,
                        )
                    ),
                )
                .order_by(SocialBenefitRedemption.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if not row:
            return SocialBenefitService._empty_social_snapshot()
        return {
            "social_applied": True,
            "social_identity_code": row.identity_code,
            "social_discount": str(money(row.subsidy_amount or 0)),
        }

    @staticmethod
    async def orders_social_map(
        db: AsyncSession, order_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        """批量：order_id → social snapshot（列表页用）。"""
        ids = sorted({int(x) for x in order_ids if x})
        if not ids:
            return {}
        rows = (
            await db.execute(
                select(SocialBenefitRedemption)
                .where(
                    SocialBenefitRedemption.order_id.in_(ids),
                    SocialBenefitRedemption.status.in_(
                        (
                            SocialBenefitService.REDEEM_PENDING,
                            SocialBenefitService.REDEEM_NORMAL,
                        )
                    ),
                )
                .order_by(SocialBenefitRedemption.id.desc())
            )
        ).scalars().all()
        out: dict[int, dict[str, Any]] = {}
        for r in rows:
            oid = int(r.order_id)
            if oid in out:
                continue  # 已按 id desc，保留最新一条
            out[oid] = {
                "social_applied": True,
                "social_identity_code": r.identity_code,
                "social_discount": str(money(r.subsidy_amount or 0)),
            }
        return out

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
    def social_beats_member(*, social_subsidy: Decimal, member_discount: Decimal) -> bool:
        """公益是否严优于会员折（补贴更大 = 折扣更低）。

        相等时用会员折，避免无谓占用公益日限次。
        例：公益 9.5 折 vs 会员 8 折 → 会员更优，返回 False。
        """
        return money(social_subsidy) > money(member_discount)

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
            reason = await SocialBenefitService.explain_ineligible(
                db,
                customer_id=customer_id,
                store_id=store_id,
                product_ids=product_ids,
                category_ids=category_ids,
            )
            return {"eligible": False, "reason": reason}
        rule, identity_code = hit
        rate = money(rule.discount_rate)
        # 补贴仅作用于可打折部分；特价/不可打折餐品与打包费原价
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
        pending_q = select(func.count()).select_from(CustomerSocialIdentity).where(
            CustomerSocialIdentity.status == SocialBenefitService.PENDING
        )
        if store_id is not None:
            pending_q = pending_q.where(
                or_(
                    CustomerSocialIdentity.store_id == int(store_id),
                    CustomerSocialIdentity.store_id.is_(None),
                )
            )
        pending = int((await db.execute(pending_q)).scalar() or 0)
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
        tmpl_id = int(tmpl.id)
        for cid in identities:
            key = f"love_day:{day_key}:{int(cid)}:{tmpl_id}"
            try:
                rows = await CouponService.issue_by_template_id(
                    db,
                    int(cid),
                    tmpl_id,
                    quantity=1,
                    client_request_id=key,
                )
                # 单客 commit：长名单中途崩溃时已发券不整批回滚（幂等键 love_day:…）
                await db.commit()
                if rows:
                    issued += 1
                else:
                    skipped += 1
            except AppException:
                await db.rollback()
                skipped += 1
            except Exception:
                await db.rollback()
                errors += 1
                logger.exception("爱心日发券失败 customer_id=%s", cid)
        return {
            "date": day_key,
            "template_id": tmpl_id,
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

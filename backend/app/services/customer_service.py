import hashlib
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.biz_no import BIZ_CU, BIZ_OT, generate_no, resolve_table_parts
from app.core.exceptions import AppException
from app.core.response import money, now_local, serialize_dt
from app.models import (
    Customer,
    CustomerAlipayAccount,
    CustomerIdentity,
    CustomerPreference,
    CustomerWechatAccount,
    DiningTable,
    MemberLevel,
    Order,
    OrderItem,
    OrderItemOption,
    PointLedger,
    Store,
)
from app.services.order_service import OrderPriceCalculator

DEFAULT_MEMBER_LEVEL_NAME = "普通会员"
PLACEHOLDER_NICKNAMES = frozenset({"联调员", "面馆会员", "微信用户", "支付宝用户"})


class CustomerService:
    @staticmethod
    def keyword_condition(keyword: str | None):
        """昵称 / 显示名 / 手机 / 顾客编号 模糊匹配；纯数字时额外精确匹配 ID。"""
        kw = (keyword or "").strip()
        if not kw:
            return None
        like = f"%{kw}%"
        clauses = [
            Customer.display_name.like(like),
            Customer.nickname.like(like),
            Customer.phone.like(like),
            Customer.customer_no.like(like),
        ]
        if kw.isdigit():
            clauses.append(Customer.id == int(kw))
        return or_(*clauses)

    @staticmethod
    async def search_active_customers(
        db: AsyncSession,
        keyword: str,
        *,
        page: int = 1,
        page_size: int = 20,
        store_id: int | None = None,
    ) -> tuple[list[Customer], int]:
        """搜索启用顾客。store_id 非空时限定本店注册或本店下过单的顾客。"""
        kw = (keyword or "").strip()
        if not kw:
            return [], 0
        cond = CustomerService.keyword_condition(kw)
        q = select(Customer).where(Customer.status == 1).where(cond)
        if store_id is not None:
            from app.models import Order

            q = q.where(
                or_(
                    Customer.registered_store_id == int(store_id),
                    Customer.id.in_(
                        select(Order.customer_id).where(
                            Order.store_id == int(store_id),
                            Order.customer_id.isnot(None),
                        )
                    ),
                )
            )
        total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
        page = max(1, page)
        page_size = min(max(1, page_size), 50)
        rows = list(
            (
                await db.execute(
                    q.order_by(Customer.created_at.desc(), Customer.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
        )
        return rows, int(total)

    @staticmethod
    def is_member(customer: Customer) -> bool:
        return str(getattr(customer, "member_status", "") or "").upper() == "MEMBER"

    @staticmethod
    def member_payload(customer: Customer, level: MemberLevel | None = None) -> dict:
        """顾客会员关联摘要（列表/详情复用）。以 member_status 为准，不再用昵称/手机启发式。"""
        registered = CustomerService.is_member(customer)
        return {
            "is_member": registered,
            "member_status": str(getattr(customer, "member_status", None) or "GUEST").upper(),
            "account_status": str(getattr(customer, "account_status", None) or "ACTIVE").upper(),
            "profile_status": str(getattr(customer, "profile_status", None) or "EMPTY").upper(),
            "phone_verified": bool(int(getattr(customer, "phone_verified", 0) or 0)),
            "member_activated_at": (
                serialize_dt(customer.member_activated_at)
                if getattr(customer, "member_activated_at", None)
                else None
            ),
            "member_level_id": customer.member_level_id,
            "member_level_name": level.name if level else None,
            "member_discount_rate": str(level.discount_rate) if level else None,
            "member_min_points": int(level.min_points) if level else None,
            "member_points": int(customer.points or 0),
            "balance_principal": str(money(getattr(customer, "balance_principal", 0) or 0)),
            "balance_gift": str(money(getattr(customer, "balance_gift", 0) or 0)),
            "balance_total": str(
                money(getattr(customer, "balance_principal", 0) or 0)
                + money(getattr(customer, "balance_gift", 0) or 0)
            ),
        }

    @staticmethod
    def compute_profile_status(customer: Customer) -> str:
        has_nick = CustomerService.is_meaningful_nickname(customer.nickname or customer.display_name)
        has_phone = bool(customer.phone and str(customer.phone).strip())
        has_avatar = bool(customer.avatar_url and str(customer.avatar_url).strip())
        if has_nick and has_phone:
            return "COMPLETED"
        if has_nick or has_phone or has_avatar:
            return "PARTIAL"
        return "EMPTY"

    @staticmethod
    def refresh_profile_status(customer: Customer) -> str:
        status = CustomerService.compute_profile_status(customer)
        customer.profile_status = status
        return status

    @staticmethod
    def _new_guest_customer() -> Customer:
        now = now_local()
        return Customer(
            customer_no=generate_no(BIZ_CU),
            status=1,
            member_status="GUEST",
            account_status="ACTIVE",
            profile_status="EMPTY",
            phone_verified=0,
            first_seen_at=now,
        )

    @staticmethod
    def discount_display_text(rate: Decimal | None) -> str | None:
        if rate is None:
            return None
        r = money(rate)
        if r >= Decimal("1"):
            return None
        zhe = (r * Decimal("10")).quantize(Decimal("0.1"))
        return f"{zhe}折"

    @staticmethod
    async def list_levels_ordered(db: AsyncSession) -> list[MemberLevel]:
        return list(
            (await db.execute(select(MemberLevel).order_by(MemberLevel.min_points.asc()))).scalars().all()
        )

    @staticmethod
    def level_admin_payload(level: MemberLevel, member_count: int = 0) -> dict:
        """后台会员等级配置展示。"""
        rate = money(level.discount_rate)
        is_base = level.name == DEFAULT_MEMBER_LEVEL_NAME
        return {
            "id": level.id,
            "name": level.name,
            "min_points": int(level.min_points),
            "discount_rate": str(rate),
            "discount_text": CustomerService.discount_display_text(rate),
            "is_base": is_base,
            "editable_min_points": not is_base,
            "member_count": int(member_count),
        }

    @staticmethod
    def resolve_level_updates(levels: list, items: list[dict]) -> list[tuple]:
        """校验银卡/金卡等升级积分与折扣；返回 [(level, min_points, discount_rate), ...]。"""
        if not items:
            raise AppException(40090, "未提交任何修改")
        by_id = {int(lv.id): lv for lv in levels}
        seen: set[int] = set()
        patch: dict[int, dict] = {}
        for raw in items:
            lid = int(raw["id"])
            if lid in seen:
                raise AppException(40091, "提交的等级 ID 重复")
            seen.add(lid)
            if lid not in by_id:
                raise AppException(40091, f"会员等级不存在: {lid}")
            patch[lid] = raw

        planned: list[tuple] = []
        for lv in levels:
            raw = patch.get(int(lv.id))
            min_pts = int(raw["min_points"]) if raw and "min_points" in raw else int(lv.min_points)
            if raw and raw.get("discount_rate") is not None:
                rate = money(raw["discount_rate"])
            else:
                rate = money(lv.discount_rate)
            if min_pts < 0:
                raise AppException(40092, f"「{lv.name}」升级积分不能为负数")
            if rate <= Decimal("0") or rate > Decimal("1"):
                raise AppException(40093, f"「{lv.name}」折扣率须在 0 到 1 之间（如 0.98 表示 9.8 折）")
            if lv.name == DEFAULT_MEMBER_LEVEL_NAME and min_pts != 0:
                raise AppException(40094, "普通会员为起始档，升级积分须为 0")
            planned.append((lv, min_pts, rate))

        ordered = planned
        for i in range(1, len(ordered)):
            prev_name, prev_pts = ordered[i - 1][0].name, ordered[i - 1][1]
            name, pts = ordered[i][0].name, ordered[i][1]
            if pts <= prev_pts:
                raise AppException(
                    40095,
                    f"升级积分须随等级递增：{prev_name}（{prev_pts}）须低于 {name}（{pts}）",
                )
        return planned

    @staticmethod
    async def apply_level_updates(
        db: AsyncSession, items: list[dict], *, recalculate: bool = True
    ) -> dict:
        """批量更新会员等级门槛/折扣，可选按积分重算全部顾客等级。"""
        levels = await CustomerService.list_levels_ordered(db)
        if not levels:
            raise AppException(40096, "尚未配置会员等级")
        planned = CustomerService.resolve_level_updates(levels, items)
        changed: list[dict] = []
        for lv, min_pts, rate in planned:
            before_pts = int(lv.min_points)
            before_rate = money(lv.discount_rate)
            if before_pts == min_pts and before_rate == rate:
                continue
            lv.min_points = min_pts
            lv.discount_rate = rate
            changed.append(
                {
                    "id": lv.id,
                    "name": lv.name,
                    "before": {"min_points": before_pts, "discount_rate": str(before_rate)},
                    "after": {"min_points": min_pts, "discount_rate": str(rate)},
                }
            )
        await db.flush()
        recalc_count = 0
        if recalculate:
            recalc_count = await CustomerService.recalculate_all_member_levels(db)
        return {"changed": changed, "recalculated": recalc_count}

    @staticmethod
    async def recalculate_all_member_levels(db: AsyncSession) -> int:
        """按当前门槛把全部顾客挂到可达最高档。"""
        levels = await CustomerService.list_levels_ordered(db)
        if not levels:
            return 0
        total = int(await db.scalar(select(func.count()).select_from(Customer)) or 0)
        await db.execute(update(Customer).values(member_level_id=levels[0].id))
        for lv in levels[1:]:
            await db.execute(
                update(Customer)
                .where(Customer.points >= int(lv.min_points))
                .values(member_level_id=lv.id)
            )
        await db.flush()
        return total

    @staticmethod
    async def recalculate_member_level(db: AsyncSession, customer: Customer) -> MemberLevel | None:
        """按积分重算并写入会员等级（取可达最高档）。"""
        levels = await CustomerService.list_levels_ordered(db)
        if not levels:
            return None
        pts = int(customer.points or 0)
        chosen = levels[0]
        for lv in levels:
            if pts >= int(lv.min_points):
                chosen = lv
        if customer.member_level_id != chosen.id:
            customer.member_level_id = chosen.id
            await db.flush()
        return chosen

    @staticmethod
    def points_from_amount(amount) -> int:
        """实付金额 → 积分：按元四舍五入（与 money 同 ROUND_HALF_UP）。"""
        from decimal import ROUND_HALF_UP

        return int(money(amount or 0).to_integral_value(rounding=ROUND_HALF_UP))

    @staticmethod
    async def resolve_birthday_benefit_mode(
        db: AsyncSession, store_id: int | None
    ) -> str:
        """生日优惠方式；兼容旧键 birthday_points_enabled。"""
        from app.services.system_settings_service import SystemSettingsService
        from app.utils.birthday import MODE_OFF, MODE_POINTS, normalize_benefit_mode

        raw = (
            await SystemSettingsService.effective_setting(
                db, "birthday_benefit_mode", store_id=store_id, fallback=""
            )
        ).strip()
        if raw:
            return normalize_benefit_mode(raw)
        # 旧库仅有 birthday_points_enabled
        legacy_on = await SystemSettingsService.effective_bool(
            db, "birthday_points_enabled", True, store_id=store_id
        )
        return MODE_POINTS if legacy_on else MODE_OFF

    @staticmethod
    async def birthday_benefit_payload(
        db: AsyncSession,
        store_id: int | None,
        customer: Customer | None = None,
        *,
        try_issue_coupon: bool = False,
    ) -> dict:
        """门店生日权益说明；传入 customer 时附带今日是否生日与展示文案。

        try_issue_coupon=True 仅用于顾客端（会员中心）：今日生日且配置含赠券时系统发券。
        """
        from app.services.coupon_service import CouponService
        from app.services.system_settings_service import SystemSettingsService
        from app.utils.birthday import (
            MODE_BOTH,
            MODE_COUPON,
            benefit_summary,
            format_birthday_display,
            is_birthday_today,
            normalize_calendar,
        )

        mode = await CustomerService.resolve_birthday_benefit_mode(db, store_id)
        multiplier = await SystemSettingsService.effective_int(
            db, "birthday_points_multiplier", 2, store_id=store_id
        )
        coupon_name = await CouponService.birthday_coupon_template_name(db, store_id=store_id)
        cal = normalize_calendar(getattr(customer, "birthday_calendar", None)) if customer else None
        today = now_local().date()
        is_today = bool(
            customer and is_birthday_today(customer.birthday, cal, today=today)
        )
        summary = benefit_summary(
            mode=mode,
            multiplier=multiplier,
            coupon_name=coupon_name,
            calendar=cal,
            is_today=is_today,
        )
        if customer is not None:
            summary["birthday_display"] = format_birthday_display(customer.birthday, cal)
            summary["birthday_calendar"] = cal
            summary["birthday_calendar_label"] = "农历" if cal == "LUNAR" else "公历"
            summary["birthday_coupon_issued"] = False
            if (
                try_issue_coupon
                and is_today
                and mode in (MODE_COUPON, MODE_BOTH)
                and coupon_name
            ):
                issued = await CouponService.issue_birthday_coupon(
                    db, int(customer.id), store_id=store_id, year=today.year
                )
                summary["birthday_coupon_issued"] = bool(issued)
        return summary

    @staticmethod
    async def award_points_for_paid_order(db: AsyncSession, order: Order) -> int:
        """支付成功：实付 1 元 = 1 积分，并尝试升级等级。行锁 + point_ledger。"""
        if not order.customer_id:
            return 0
        customer = (
            await db.execute(
                select(Customer).where(Customer.id == int(order.customer_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if not customer:
            return 0
        # 同订单幂等：已有 EARN 流水则跳过
        existed = (
            await db.execute(
                select(PointLedger.id)
                .where(
                    PointLedger.order_id == order.id,
                    PointLedger.entry_type == "EARN",
                )
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if existed:
            return 0
        earned = CustomerService.points_from_amount(order.total_amount)
        if earned <= 0:
            return 0
        # 生日权益：按门店业务参数 + 顾客公历/农历匹配
        from app.services.coupon_service import CouponService
        from app.services.system_settings_service import SystemSettingsService
        from app.utils.birthday import (
            MODE_BOTH,
            MODE_COUPON,
            MODE_POINTS,
            is_birthday_today,
            normalize_calendar,
        )

        store_id = int(order.store_id) if order.store_id else None
        mode = await CustomerService.resolve_birthday_benefit_mode(db, store_id)
        multiplier = await SystemSettingsService.effective_int(
            db, "birthday_points_multiplier", 2, store_id=store_id
        )
        today = now_local().date()
        is_bday = is_birthday_today(
            customer.birthday,
            normalize_calendar(getattr(customer, "birthday_calendar", None)),
            today=today,
        )
        if is_bday and mode in (MODE_POINTS, MODE_BOTH) and multiplier > 1:
            earned *= max(1, int(multiplier))
        if is_bday and mode in (MODE_COUPON, MODE_BOTH):
            await CouponService.issue_birthday_coupon(
                db, int(customer.id), store_id=store_id, year=today.year
            )
        customer.points = int(customer.points or 0) + earned
        db.add(
            PointLedger(
                customer_id=customer.id,
                order_id=order.id,
                refund_id=None,
                delta=earned,
                balance_after=int(customer.points),
                entry_type="EARN",
            )
        )
        await CustomerService.recalculate_member_level(db, customer)
        await db.flush()
        return earned

    @staticmethod
    async def deduct_points_for_refund(
        db: AsyncSession,
        order: Order,
        refund_amount: Decimal,
        *,
        refund_id: int | None = None,
    ) -> int:
        """退款成功：按退款金额扣积分，不超过本单净已发积分。行锁 + point_ledger。"""
        from sqlalchemy import func

        if not order.customer_id:
            return 0
        customer = (
            await db.execute(
                select(Customer).where(Customer.id == int(order.customer_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if not customer:
            return 0
        if refund_id is not None:
            existed = (
                await db.execute(
                    select(PointLedger.id)
                    .where(
                        PointLedger.refund_id == int(refund_id),
                        PointLedger.entry_type == "REFUND_DEDUCT",
                    )
                    .limit(1)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existed:
                return 0
        deducted = CustomerService.points_from_amount(refund_amount)
        if deducted <= 0:
            return 0
        # 本单净已发积分上限，避免跨单扣光余额
        earned = int(
            await db.scalar(
                select(func.coalesce(func.sum(PointLedger.delta), 0)).where(
                    PointLedger.order_id == order.id,
                    PointLedger.entry_type == "EARN",
                )
            )
            or 0
        )
        already = int(
            await db.scalar(
                select(func.coalesce(func.sum(PointLedger.delta), 0)).where(
                    PointLedger.order_id == order.id,
                    PointLedger.entry_type == "REFUND_DEDUCT",
                )
            )
            or 0
        )
        # already 为负，净可扣 = earned + already
        remaining = max(0, earned + already)
        before = int(customer.points or 0)
        actual = min(before, deducted, remaining)
        customer.points = before - actual
        if actual > 0:
            db.add(
                PointLedger(
                    customer_id=customer.id,
                    order_id=order.id,
                    refund_id=refund_id,
                    delta=-actual,
                    balance_after=int(customer.points),
                    entry_type="REFUND_DEDUCT",
                )
            )
        await CustomerService.recalculate_member_level(db, customer)
        await db.flush()
        return actual

    @staticmethod
    def member_progress_payload(
        customer: Customer, level: MemberLevel | None, all_levels: list[MemberLevel]
    ) -> dict:
        points = int(customer.points or 0)
        current_min = int(level.min_points) if level else 0
        next_level: MemberLevel | None = None
        for lv in all_levels:
            if int(lv.min_points) > points:
                next_level = lv
                break
        points_to_next = max(0, int(next_level.min_points) - points) if next_level else 0
        progress_pct = 100
        if next_level and level:
            span = int(next_level.min_points) - current_min
            if span > 0:
                progress_pct = min(100, max(0, int((points - current_min) / span * 100)))
        levels_public = []
        for lv in all_levels:
            rate = money(lv.discount_rate)
            levels_public.append(
                {
                    "id": lv.id,
                    "name": lv.name,
                    "min_points": int(lv.min_points),
                    "discount_rate": str(rate),
                    "discount_text": CustomerService.discount_display_text(rate),
                    "is_current": bool(level and lv.id == level.id),
                    "unlocked": points >= int(lv.min_points),
                }
            )
        return {
            "next_level_name": next_level.name if next_level else None,
            "next_level_min_points": int(next_level.min_points) if next_level else None,
            "points_to_next_level": points_to_next,
            "level_progress_percent": progress_pct,
            "member_levels": levels_public,
            "points_rule": "实付1元=1积分，积分达标自动升级享对应折扣",
        }

    @staticmethod
    async def build_member_profile(db: AsyncSession, customer: Customer) -> dict:
        level = None
        if customer.member_level_id:
            level = await db.get(MemberLevel, customer.member_level_id)
        all_levels = await CustomerService.list_levels_ordered(db)
        if not level and all_levels:
            level = all_levels[0]
        return {
            **CustomerService.member_payload(customer, level),
            **CustomerService.member_progress_payload(customer, level, all_levels),
        }

    @staticmethod
    async def load_levels_map(db: AsyncSession, level_ids: list[int | None]) -> dict[int, MemberLevel]:
        ids = list({int(i) for i in level_ids if i})
        if not ids:
            return {}
        rows = (await db.execute(select(MemberLevel).where(MemberLevel.id.in_(ids)))).scalars().all()
        return {m.id: m for m in rows}

    @staticmethod
    async def ensure_default_member_level(db: AsyncSession, customer: Customer) -> MemberLevel | None:
        """注册会员时若无等级则挂「普通会员」。"""
        if customer.member_level_id:
            return await db.get(MemberLevel, customer.member_level_id)
        level = (
            await db.execute(select(MemberLevel).where(MemberLevel.name == DEFAULT_MEMBER_LEVEL_NAME).limit(1))
        ).scalar_one_or_none()
        if not level:
            level = (
                await db.execute(select(MemberLevel).order_by(MemberLevel.min_points.asc()).limit(1))
            ).scalar_one_or_none()
        if level:
            customer.member_level_id = level.id
            await db.flush()
        return level

    @staticmethod
    async def resolve_member_discount_rate(db: AsyncSession, customer_id: int | None) -> Decimal:
        """会员等级折扣率（0~1）；游客或无等级时返回 1（不打折）。"""
        if not customer_id:
            return Decimal("1")
        customer = await db.get(Customer, int(customer_id))
        if not customer or not CustomerService.is_member(customer) or not customer.member_level_id:
            return Decimal("1")
        level = await db.get(MemberLevel, customer.member_level_id)
        if not level or level.discount_rate is None:
            return Decimal("1")
        rate = money(level.discount_rate)
        if rate <= Decimal("0") or rate > Decimal("1"):
            return Decimal("1")
        return rate

    @staticmethod
    def calc_member_discount(
        subtotal: Decimal,
        packing_fee: Decimal,
        rate: Decimal,
        *,
        discountable_subtotal: Decimal | None = None,
    ) -> tuple[Decimal, Decimal]:
        """按可打折商品小计计算会员优惠（打包费、特价商品不参与折扣）。返回 (discount_amount, total_amount)。"""
        subtotal = money(subtotal)
        discountable_subtotal = money(discountable_subtotal if discountable_subtotal is not None else subtotal)
        if discountable_subtotal > subtotal:
            discountable_subtotal = subtotal
        packing_fee = money(packing_fee)
        rate = money(rate)
        if rate >= Decimal("1"):
            return Decimal("0.00"), money(subtotal + packing_fee)
        discount = money(discountable_subtotal * (Decimal("1") - rate))
        total = money(subtotal - discount + packing_fee)
        return discount, total

    @staticmethod
    def calc_coupon_eligible_subtotal(
        subtotal: Decimal,
        discountable_subtotal: Decimal,
        discount_amount: Decimal,
    ) -> Decimal:
        """会员折后可用于优惠券的商品小计（不含特价商品）。"""
        subtotal = money(subtotal)
        discountable_subtotal = money(discountable_subtotal)
        if discountable_subtotal > subtotal:
            discountable_subtotal = subtotal
        special_subtotal = money(subtotal - discountable_subtotal)
        after_member = money(subtotal - money(discount_amount))
        eligible = money(after_member - special_subtotal)
        return eligible if eligible > Decimal("0") else Decimal("0.00")

    @staticmethod
    def calc_voucher_eligible_payable(
        coupon_eligible_subtotal: Decimal,
        coupon_discount: Decimal,
        packing_fee: Decimal,
        *,
        need_pack: bool,
    ) -> Decimal:
        """可用于代金券门槛/面额判断的基数（不含特价商品小计，可选含打包费）。"""
        base = money(coupon_eligible_subtotal) - money(coupon_discount)
        if base < Decimal("0"):
            base = Decimal("0.00")
        if need_pack:
            base = money(base + money(packing_fee))
        return base

    @staticmethod
    async def resolve_wechat_openid(
        db: AsyncSession,
        customer_id: int,
        *,
        prefer_openid: str | None = None,
    ) -> str | None:
        """同一顾客可能绑定多个微信 openid（手机号合并/unionid），取优先或最新一条。"""
        prefer = (prefer_openid or "").strip()
        if prefer:
            matched = (
                await db.execute(
                    select(CustomerWechatAccount.openid)
                    .where(
                        CustomerWechatAccount.customer_id == customer_id,
                        CustomerWechatAccount.openid == prefer,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if matched:
                return matched
        return (
            await db.execute(
                select(CustomerWechatAccount.openid)
                .where(CustomerWechatAccount.customer_id == customer_id)
                .order_by(CustomerWechatAccount.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def resolve_alipay_user_id(db: AsyncSession, customer_id: int) -> str | None:
        """取顾客绑定的支付宝用户号（buyer_id），供小程序 JSAPI 创单。"""
        return (
            await db.execute(
                select(CustomerAlipayAccount.alipay_user_id)
                .where(CustomerAlipayAccount.customer_id == customer_id)
                .order_by(CustomerAlipayAccount.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _identity_customer_id(
        db: AsyncSession, provider: str, provider_subject: str | None
    ) -> int | None:
        subject = (provider_subject or "").strip()
        if not subject:
            return None
        return (
            await db.execute(
                select(CustomerIdentity.customer_id).where(
                    CustomerIdentity.provider == provider,
                    CustomerIdentity.provider_subject == subject,
                    CustomerIdentity.verified == 1,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _claim_identity(
        db: AsyncSession,
        *,
        customer_id: int,
        provider: str,
        provider_subject: str | None,
    ) -> None:
        """原子认领稳定身份；同一身份绝不能静默改挂到另一顾客。"""
        subject = (provider_subject or "").strip()
        if not subject:
            return
        existing = (
            await db.execute(
                select(CustomerIdentity).where(
                    CustomerIdentity.provider == provider,
                    CustomerIdentity.provider_subject == subject,
                )
            )
        ).scalar_one_or_none()
        if existing:
            if int(existing.customer_id) != int(customer_id):
                raise AppException(40905, "该渠道身份已绑定其他顾客，请联系客服处理")
            if int(existing.verified or 0) != 1:
                existing.verified = 1
            return
        try:
            async with db.begin_nested():
                db.add(
                    CustomerIdentity(
                        customer_id=int(customer_id),
                        provider=provider,
                        provider_subject=subject,
                        verified=1,
                    )
                )
                await db.flush()
        except IntegrityError:
            winner = (
                await db.execute(
                    select(CustomerIdentity).where(
                        CustomerIdentity.provider == provider,
                        CustomerIdentity.provider_subject == subject,
                    )
                )
            ).scalar_one_or_none()
            if not winner or int(winner.customer_id) != int(customer_id):
                raise AppException(40905, "该渠道身份已绑定其他顾客，请联系客服处理")

    @staticmethod
    async def _ensure_wechat_account(
        db: AsyncSession,
        *,
        customer_id: int,
        openid: str,
        unionid: str | None,
        copy_profile_from: CustomerWechatAccount | None = None,
    ) -> None:
        account = (
            await db.execute(select(CustomerWechatAccount).where(CustomerWechatAccount.openid == openid))
        ).scalar_one_or_none()
        if account:
            if int(account.customer_id) != int(customer_id):
                raise AppException(40905, "微信身份绑定冲突，请联系客服处理")
            if unionid and not account.unionid:
                account.unionid = unionid
        else:
            src = copy_profile_from
            try:
                async with db.begin_nested():
                    db.add(
                        CustomerWechatAccount(
                            customer_id=int(customer_id),
                            openid=openid,
                            unionid=unionid,
                            profile_completed=int(getattr(src, "profile_completed", 0) or 0),
                            nickname=getattr(src, "nickname", None),
                            avatar_url=getattr(src, "avatar_url", None),
                        )
                    )
                    await db.flush()
            except IntegrityError:
                account = (
                    await db.execute(
                        select(CustomerWechatAccount).where(CustomerWechatAccount.openid == openid)
                    )
                ).scalar_one_or_none()
                if not account or int(account.customer_id) != int(customer_id):
                    raise AppException(40905, "微信身份绑定冲突，请联系客服处理")
        await CustomerService._claim_identity(
            db,
            customer_id=customer_id,
            provider="WECHAT_OPENID",
            provider_subject=openid,
        )
        if unionid:
            await CustomerService._claim_identity(
                db,
                customer_id=customer_id,
                provider="WECHAT_UNIONID",
                provider_subject=unionid,
            )
        await db.flush()

    @staticmethod
    async def get_or_create_by_wechat(db: AsyncSession, openid: str, unionid: str | None = None) -> Customer:
        openid = (openid or "").strip()
        unionid = (unionid or "").strip() or None
        if not openid:
            raise AppException(40002, "微信未返回稳定用户身份")

        openid_customer_id = await CustomerService._identity_customer_id(db, "WECHAT_OPENID", openid)
        union_customer_id = await CustomerService._identity_customer_id(db, "WECHAT_UNIONID", unionid)
        if openid_customer_id and union_customer_id and openid_customer_id != union_customer_id:
            raise AppException(40905, "微信身份数据冲突，请联系客服处理")
        identity_customer_id = openid_customer_id or union_customer_id
        if identity_customer_id:
            await CustomerService._ensure_wechat_account(
                db,
                customer_id=identity_customer_id,
                openid=openid,
                unionid=unionid,
            )
            return await db.get(Customer, identity_customer_id)

        account = (
            await db.execute(select(CustomerWechatAccount).where(CustomerWechatAccount.openid == openid))
        ).scalar_one_or_none()
        if account:
            await CustomerService._ensure_wechat_account(
                db,
                customer_id=account.customer_id,
                openid=openid,
                unionid=unionid,
            )
            return await db.get(Customer, account.customer_id)

        # 兼容迁移前数据：仅 UnionID 这一微信官方强身份可复用既有 Customer。
        union_acc = None
        if unionid:
            union_acc = (
                await db.execute(
                    select(CustomerWechatAccount)
                    .where(CustomerWechatAccount.unionid == unionid)
                    .order_by(CustomerWechatAccount.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
        if union_acc:
            await CustomerService._ensure_wechat_account(
                db,
                customer_id=union_acc.customer_id,
                openid=openid,
                unionid=unionid,
                copy_profile_from=union_acc,
            )
            return await db.get(Customer, union_acc.customer_id)

        try:
            async with db.begin_nested():
                customer = CustomerService._new_guest_customer()
                db.add(customer)
                await db.flush()
                db.add(CustomerWechatAccount(customer_id=customer.id, openid=openid, unionid=unionid))
                db.add(
                    CustomerIdentity(
                        customer_id=customer.id,
                        provider="WECHAT_OPENID",
                        provider_subject=openid,
                        verified=1,
                    )
                )
                if unionid:
                    db.add(
                        CustomerIdentity(
                            customer_id=customer.id,
                            provider="WECHAT_UNIONID",
                            provider_subject=unionid,
                            verified=1,
                        )
                    )
                await db.flush()
                return customer
        except IntegrityError:
            winner_id = await CustomerService._identity_customer_id(db, "WECHAT_OPENID", openid)
            union_winner_id = await CustomerService._identity_customer_id(db, "WECHAT_UNIONID", unionid)
            if winner_id and union_winner_id and winner_id != union_winner_id:
                raise AppException(40905, "微信身份数据冲突，请联系客服处理")
            winner_id = winner_id or union_winner_id
            if winner_id:
                await CustomerService._ensure_wechat_account(
                    db,
                    customer_id=winner_id,
                    openid=openid,
                    unionid=unionid,
                )
                return await db.get(Customer, winner_id)
            raise AppException(40002, "微信登录冲突，请重试")

    @staticmethod
    async def get_or_create_by_alipay(db: AsyncSession, alipay_user_id: str) -> Customer:
        alipay_user_id = (alipay_user_id or "").strip()
        if not alipay_user_id:
            raise AppException(40002, "支付宝未返回稳定用户身份")
        identity_customer_id = await CustomerService._identity_customer_id(
            db, "ALIPAY_USER_ID", alipay_user_id
        )
        account = (
            await db.execute(
                select(CustomerAlipayAccount).where(
                    CustomerAlipayAccount.alipay_user_id == alipay_user_id
                )
            )
        ).scalar_one_or_none()
        if identity_customer_id:
            if account and int(account.customer_id) != int(identity_customer_id):
                raise AppException(40905, "支付宝身份绑定冲突，请联系客服处理")
            if not account:
                db.add(
                    CustomerAlipayAccount(
                        customer_id=identity_customer_id,
                        alipay_user_id=alipay_user_id,
                    )
                )
                await db.flush()
            return await db.get(Customer, identity_customer_id)
        if account:
            await CustomerService._claim_identity(
                db,
                customer_id=account.customer_id,
                provider="ALIPAY_USER_ID",
                provider_subject=alipay_user_id,
            )
            return await db.get(Customer, account.customer_id)
        try:
            async with db.begin_nested():
                customer = CustomerService._new_guest_customer()
                db.add(customer)
                await db.flush()
                db.add(CustomerAlipayAccount(customer_id=customer.id, alipay_user_id=alipay_user_id))
                db.add(
                    CustomerIdentity(
                        customer_id=customer.id,
                        provider="ALIPAY_USER_ID",
                        provider_subject=alipay_user_id,
                        verified=1,
                    )
                )
                await db.flush()
                return customer
        except IntegrityError:
            winner_id = await CustomerService._identity_customer_id(
                db, "ALIPAY_USER_ID", alipay_user_id
            )
            if winner_id:
                return await db.get(Customer, winner_id)
            again = (
                await db.execute(
                    select(CustomerAlipayAccount).where(
                        CustomerAlipayAccount.alipay_user_id == alipay_user_id
                    )
                )
            ).scalar_one_or_none()
            if again:
                await CustomerService._claim_identity(
                    db,
                    customer_id=again.customer_id,
                    provider="ALIPAY_USER_ID",
                    provider_subject=alipay_user_id,
                )
                return await db.get(Customer, again.customer_id)
            raise AppException(40002, "支付宝登录冲突，请重试")

    @staticmethod
    def is_meaningful_nickname(name: str | None) -> bool:
        n = (name or "").strip()
        return bool(n) and n not in PLACEHOLDER_NICKNAMES

    @staticmethod
    async def _channel_accounts(db: AsyncSession, customer_id: int, channel: str):
        ch = (channel or "").strip().lower()
        if ch == "wechat":
            return list(
                (
                    await db.execute(
                        select(CustomerWechatAccount)
                        .where(CustomerWechatAccount.customer_id == customer_id)
                        .order_by(CustomerWechatAccount.id.desc())
                    )
                ).scalars().all()
            )
        if ch == "alipay":
            return list(
                (
                    await db.execute(
                        select(CustomerAlipayAccount)
                        .where(CustomerAlipayAccount.customer_id == customer_id)
                        .order_by(CustomerAlipayAccount.id.desc())
                    )
                ).scalars().all()
            )
        return []

    @staticmethod
    async def is_channel_profile_completed(db: AsyncSession, customer_id: int, channel: str) -> bool:
        """当前渠道是否已点过「注册」；未完成时不得沿用另一端（微信/支付宝）资料。"""
        ch = (channel or "").strip().lower()
        if ch not in ("wechat", "alipay"):
            customer = await db.get(Customer, customer_id)
            return bool(
                customer and CustomerService.is_meaningful_nickname(customer.nickname or customer.display_name)
            )
        rows = await CustomerService._channel_accounts(db, customer_id, ch)
        if not rows:
            return False
        return int(getattr(rows[0], "profile_completed", 0) or 0) == 1

    @staticmethod
    async def mark_channel_profile_completed(db: AsyncSession, customer_id: int, channel: str) -> None:
        await CustomerService.save_channel_profile(db, customer_id, channel, completed=True)

    @staticmethod
    async def save_channel_profile(
        db: AsyncSession,
        customer_id: int,
        channel: str,
        *,
        nickname: str | None = None,
        avatar_url: str | None = None,
        completed: bool | None = None,
    ) -> None:
        """只写本渠道账号上的昵称/头像，不覆盖另一端。"""
        rows = await CustomerService._channel_accounts(db, customer_id, channel)
        if not rows:
            return
        for row in rows:
            if nickname is not None:
                n = str(nickname).strip()
                if n:
                    row.nickname = n
            if avatar_url is not None:
                a = str(avatar_url).strip()
                if a:
                    row.avatar_url = a
            if completed is True:
                row.profile_completed = 1
        await db.flush()

    @staticmethod
    def fill_customer_display_if_empty(
        customer: Customer,
        *,
        nickname: str | None = None,
        avatar_url: str | None = None,
    ) -> None:
        """收银台/后台只有一份展示名：仅在顾客表为空时用本渠道资料补上，不覆盖已有值。"""
        if CustomerService.is_meaningful_nickname(nickname) and not CustomerService.is_meaningful_nickname(
            customer.nickname or customer.display_name
        ):
            n = str(nickname).strip()
            customer.nickname = n
            customer.display_name = customer.display_name or n
        if avatar_url and not str(customer.avatar_url or "").strip():
            customer.avatar_url = str(avatar_url).strip()

    @staticmethod
    async def resolve_channel_profile(db: AsyncSession, customer: Customer, channel: str) -> dict:
        """小程序展示用：只返回本渠道昵称头像，不读对端资料。"""
        ch = (channel or "").strip().lower()
        rows = await CustomerService._channel_accounts(db, customer.id, ch)
        row = rows[0] if rows else None
        completed = bool(row) and int(getattr(row, "profile_completed", 0) or 0) == 1
        if not completed:
            return {"nickname": None, "avatar_url": None, "completed": False}
        nick = str(getattr(row, "nickname", None) or "").strip()
        avatar = str(getattr(row, "avatar_url", None) or "").strip()
        # 仅当另一端尚未注册时，才从顾客表回填到本渠道（避免把微信名抄到支付宝）
        other = "alipay" if ch == "wechat" else "wechat"
        other_rows = await CustomerService._channel_accounts(db, customer.id, other)
        other_done = any(int(getattr(r, "profile_completed", 0) or 0) == 1 for r in other_rows)
        if not other_done:
            fallback_nick = str(customer.nickname or customer.display_name or "").strip()
            fallback_avatar = str(customer.avatar_url or "").strip()
            dirty = False
            if not nick and CustomerService.is_meaningful_nickname(fallback_nick):
                for r in rows:
                    if not str(getattr(r, "nickname", None) or "").strip():
                        r.nickname = fallback_nick
                nick = fallback_nick
                dirty = True
            if not avatar and fallback_avatar:
                for r in rows:
                    if not str(getattr(r, "avatar_url", None) or "").strip():
                        r.avatar_url = fallback_avatar
                avatar = fallback_avatar
                dirty = True
            if dirty:
                await db.flush()
        return {
            "nickname": nick or None,
            "avatar_url": avatar or None,
            "completed": True,
        }

    @staticmethod
    async def save_unverified_phone(
        db: AsyncSession, customer: Customer, phone: str
    ) -> Customer:
        """保存手工填写手机号；永不认证、永不查找或合并其他账号。"""
        phone = (phone or "").strip()
        if not phone:
            return customer
        if int(getattr(customer, "phone_verified", 0) or 0) == 1:
            if (customer.phone or "").strip() == phone:
                return customer
            raise AppException(40055, "已验证手机号只能通过官方手机号授权更换")
        customer.phone = phone
        customer.phone_verified = 0
        CustomerService.refresh_profile_status(customer)
        await db.flush()
        return customer

    @staticmethod
    async def bind_verified_phone(
        db: AsyncSession,
        customer: Customer,
        phone: str,
        *,
        verification_source: str,
        confirm_merge: bool = False,
        request=None,
    ) -> tuple[Customer, bool]:
        """绑定官方验证手机号；号码已属其他账号时必须二次确认后完整合并。"""
        phone = (phone or "").strip()
        source = (verification_source or "").strip().upper()
        if source not in ("WECHAT", "ALIPAY"):
            raise AppException(40054, "手机号缺少可信验证来源")

        identity = (
            await db.execute(
                select(CustomerIdentity)
                .where(
                    CustomerIdentity.provider == "PHONE",
                    CustomerIdentity.provider_subject == phone,
                    CustomerIdentity.verified == 1,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

        if not identity:
            try:
                async with db.begin_nested():
                    identity = CustomerIdentity(
                        customer_id=customer.id,
                        provider="PHONE",
                        provider_subject=phone,
                        verified=1,
                    )
                    db.add(identity)
                    await db.flush()
            except IntegrityError:
                identity = (
                    await db.execute(
                        select(CustomerIdentity).where(
                            CustomerIdentity.provider == "PHONE",
                            CustomerIdentity.provider_subject == phone,
                            CustomerIdentity.verified == 1,
                        )
                    )
                ).scalar_one_or_none()
                if not identity:
                    raise AppException(40905, "手机号绑定冲突，请重试", status_code=409)

        merged = False
        if int(identity.customer_id) != int(customer.id):
            if not confirm_merge:
                raise AppException(
                    40906,
                    "该已验证手机号已关联其他账号，确认后可合并历史订单、积分和优惠券",
                    status_code=409,
                )
            from app.services.customer_merge_service import CustomerMergeService

            result = await CustomerMergeService.merge(
                db,
                target_customer_id=int(identity.customer_id),
                source_customer_id=int(customer.id),
                reason=f"用户确认通过{source}官方手机号合并",
                request=request,
            )
            customer = await db.get(Customer, int(result["target_customer_id"]))
            if not customer:
                raise AppException(40905, "账号合并失败，请重试", status_code=409)
            merged = True
        else:
            locked = (
                await db.execute(
                    select(Customer).where(Customer.id == customer.id).with_for_update()
                )
            ).scalar_one_or_none()
            if not locked or int(locked.status or 0) != 1:
                raise AppException(40102, "顾客不存在或已禁用", status_code=401)
            customer = locked

        customer.phone = phone
        customer.phone_verified = 1
        CustomerService.refresh_profile_status(customer)
        await db.flush()
        return customer, merged

    @staticmethod
    async def remember_first_seen(
        db: AsyncSession,
        customer: Customer,
        table_token: str | None = None,
        table: DiningTable | None = None,
    ) -> None:
        """记录首次扫码门店/桌台；与会员激活、registered_* 分离。"""
        now = now_local()
        if not getattr(customer, "first_seen_at", None):
            customer.first_seen_at = now
        if getattr(customer, "first_seen_store_id", None):
            await db.flush()
            return
        resolved = table
        if resolved is None:
            token = (table_token or "").strip()
            if token:
                from app.services.table_resolve import resolve_dining_table

                resolved = await resolve_dining_table(db, token)
        if not resolved:
            await db.flush()
            return
        customer.first_seen_store_id = resolved.store_id
        customer.first_seen_table_id = resolved.id
        await db.flush()

    @staticmethod
    async def activate_member(
        db: AsyncSession,
        customer: Customer,
        *,
        channel: str | None = None,
        nickname: str | None = None,
        avatar_url: str | None = None,
        table_token: str | None = None,
        mark_channel_completed: bool = True,
    ) -> tuple[Customer, bool]:
        """
        幂等开通会员：仅首次 GUEST→MEMBER 返回 newly_activated=True（可发欢迎券等副作用）。
        已是 MEMBER 时只更新可选资料，不重复副作用。
        """
        # 会员行锁覆盖状态迁移与调用方后续发券所在事务，避免双击并发均认为首次开通。
        locked = (
            await db.execute(select(Customer).where(Customer.id == customer.id).with_for_update())
        ).scalar_one_or_none()
        if not locked or int(locked.status or 0) != 1:
            raise AppException(40102, "顾客不存在或已禁用", status_code=401)
        customer = locked
        newly = not CustomerService.is_member(customer)
        if newly:
            customer.member_status = "MEMBER"
            customer.member_activated_at = now_local()
            await CustomerService.ensure_default_member_level(db, customer)
            await CustomerService.remember_registered_scan(db, customer, table_token)

        if nickname is not None or avatar_url is not None:
            ch = (channel or "").strip().lower()
            if ch in ("wechat", "alipay"):
                await CustomerService.save_channel_profile(
                    db,
                    customer.id,
                    ch,
                    nickname=nickname,
                    avatar_url=avatar_url,
                    completed=True if mark_channel_completed else None,
                )
            CustomerService.fill_customer_display_if_empty(
                customer, nickname=nickname, avatar_url=avatar_url
            )
        elif mark_channel_completed and channel:
            ch = (channel or "").strip().lower()
            if ch in ("wechat", "alipay"):
                await CustomerService.mark_channel_profile_completed(db, customer.id, ch)

        CustomerService.refresh_profile_status(customer)
        await db.flush()
        return customer, newly

    @staticmethod
    async def remember_registered_scan(
        db: AsyncSession,
        customer: Customer,
        table_token: str | None,
    ) -> None:
        """扫码注册只记第一次门店/桌台，后续换店点餐不覆盖。"""
        if getattr(customer, "registered_store_id", None):
            return
        token = (table_token or "").strip()
        if not token:
            return
        from app.services.table_resolve import resolve_dining_table

        table = await resolve_dining_table(db, token)
        if not table:
            return
        customer.registered_store_id = table.store_id
        customer.registered_table_id = table.id
        await db.flush()

    @staticmethod
    async def registered_scan_map(db: AsyncSession, customers: list[Customer]) -> dict[int, dict]:
        """批量补注册门店/桌台展示字段。"""
        store_ids = {int(c.registered_store_id) for c in customers if getattr(c, "registered_store_id", None)}
        table_ids = {int(c.registered_table_id) for c in customers if getattr(c, "registered_table_id", None)}
        stores: dict[int, Store] = {}
        tables: dict[int, DiningTable] = {}
        if store_ids:
            rows = (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars().all()
            stores = {int(s.id): s for s in rows}
        if table_ids:
            rows = (await db.execute(select(DiningTable).where(DiningTable.id.in_(table_ids)))).scalars().all()
            tables = {int(t.id): t for t in rows}
        out: dict[int, dict] = {}
        for c in customers:
            sid = getattr(c, "registered_store_id", None)
            tid = getattr(c, "registered_table_id", None)
            store = stores.get(int(sid)) if sid else None
            table = tables.get(int(tid)) if tid else None
            out[c.id] = {
                "registered_store_id": int(sid) if sid else None,
                "registered_store_no": store.store_no if store else None,
                "registered_store_name": store.name if store else None,
                "registered_table_id": int(tid) if tid else None,
                "registered_table_no": table.table_no if table else None,
                "registered_table_name": table.table_name if table else None,
            }
        return out


class OrderCreateService:
    @staticmethod
    def _is_reusable_idempotent_order(order: Order) -> bool:
        """仅待支付未结清订单可被幂等键复用。"""
        return (order.status or "") == "PENDING_PAYMENT" and (order.payment_status or "") in (
            "UNPAID",
            "PENDING",
        )

    @staticmethod
    def _scoped_idempotency_key(
        raw: str | None,
        *,
        channel: str | None,
        customer_id: int | None,
        store_id: int | None,
        operator_id: int | None,
    ) -> str | None:
        """幂等键绑定业务主体，避免全局撞 key。"""
        key = (raw or "").strip()
        if not key:
            return None
        ch = (channel or "").strip().upper()
        if ch == "POS":
            if not store_id or not operator_id:
                raise AppException(40008, "POS 下单幂等键须绑定门店与操作员")
            scope = f"pos:{int(store_id)}:{int(operator_id)}:"
        elif customer_id:
            scope = f"cust:{int(customer_id)}:"
        elif store_id:
            scope = f"store:{int(store_id)}:"
        else:
            scope = "request:"
        combined = f"{scope}{key}"
        if len(combined) <= 64:
            return combined
        # 不可直接截断：相同长前缀会碰撞并错误复用他人/另一购物车的订单。
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"{scope}sha256:{digest}"[:64]

    @staticmethod
    def _assert_idempotent_order_match(
        order: Order,
        *,
        customer_id: int | None,
        store_id: int | None,
        channel: str | None,
        operator_id: int | None,
        dining_type: str | None = None,
        table_id: int | None = None,
    ) -> None:
        """命中幂等订单后必须再次校验归属，禁止直接 return 他人订单。"""
        ch = (channel or "").strip().upper()
        if ch and (order.channel or "").strip().upper() != ch:
            raise AppException(40902, "幂等键冲突：渠道不一致")
        if store_id is not None and int(order.store_id) != int(store_id):
            raise AppException(40902, "幂等键冲突：门店不一致")
        if customer_id is not None:
            if order.customer_id is None or int(order.customer_id) != int(customer_id):
                raise AppException(40902, "幂等键冲突：顾客不一致")
        if ch == "POS" and operator_id is not None:
            oid = getattr(order, "operator_id", None)
            if oid is not None and int(oid) != int(operator_id):
                raise AppException(40902, "幂等键冲突：操作员不一致")
        # P0-15: 校验订单核心要素，防止同 key 不同参数
        dtype = (dining_type or "").strip().upper()
        if dtype:
            order_dtype = (getattr(order, "dining_type", "") or "").strip().upper()
            if order_dtype and order_dtype != dtype:
                raise AppException(40902, "幂等键冲突：就餐方式不一致")
        if table_id is not None:
            order_tid = getattr(order, "table_id", None)
            if order_tid is not None and int(order_tid) != int(table_id):
                raise AppException(40902, "幂等键冲突：桌台不一致")

    @staticmethod
    async def create_order(
        db: AsyncSession,
        *,
        store_id: int | None,
        table_token: str | None,
        customer_id: int | None,
        items: list[dict],
        remark: str | None = None,
        channel: str | None = None,
        idempotency_key: str | None = None,
        need_pack: bool = False,
        dining_type: str | None = None,
        customer_coupon_id: int | None = None,
        customer_voucher_id: int | None = None,
        waive_packing_fee: bool = False,
        operator_id: int | None = None,
        skip_promotions: bool = False,
        scheduled_pickup_at: str | None = None,
        use_social_benefit: bool = False,
        social_rule_id: int | None = None,
    ) -> Order:
        from sqlalchemy.exc import IntegrityError

        from app.services.status_history import record_order_status

        table_id = None
        table_no_raw: str | None = None
        table_token = (table_token or "").strip() or None
        if table_token:
            from app.services.table_resolve import resolve_dining_table

            # 有 table_token 时以桌台解析出门店为准，避免前端缓存 store_id 与桌台不一致
            table = await resolve_dining_table(db, table_token, store_id=store_id)
            if not table:
                raise AppException(40403, "无效桌台二维码（请扫桌码，或确认页选择桌号）")
            table_id = table.id
            store_id = table.store_id
            table_no_raw = table.table_no
            # 开发用桌号作 token 时，后续支付/展示统一落到真实 qr_token 绑定的桌
        else:
            # 堂食（微信/支付宝）必须绑桌台，否则厨房/订单无法显示桌号
            ch = (channel or "").upper()
            if ch in ("WECHAT", "ALIPAY"):
                raise AppException(40008, "堂食请先扫桌台二维码，或在确认页选择桌号")
            if not store_id or int(store_id) <= 0:
                raise AppException(40008, "缺少桌台令牌或有效门店")

        scoped_key = OrderCreateService._scoped_idempotency_key(
            idempotency_key,
            channel=channel,
            customer_id=customer_id,
            store_id=store_id,
            operator_id=operator_id,
        )
        if scoped_key:
            existing = await db.execute(select(Order).where(Order.idempotency_key == scoped_key))
            order = existing.scalar_one_or_none()
            if order:
                OrderCreateService._assert_idempotent_order_match(
                    order,
                    customer_id=customer_id,
                    store_id=store_id,
                    channel=channel,
                    operator_id=operator_id,
                    dining_type=dining_type,
                    table_id=table_id,
                )
                # 仅复用待支付单；已支付/取消等同内容再下须换新幂等键（客户端带 checkout nonce）
                if not OrderCreateService._is_reusable_idempotent_order(order):
                    raise AppException(40903, "该幂等键对应订单已结束，请重新下单")
                return order

        dtype = (dining_type or "").strip().upper()
        if dtype not in ("DINE_IN", "TAKEAWAY"):
            dtype = "TAKEAWAY" if need_pack else "DINE_IN"
        pack = need_pack or dtype == "TAKEAWAY"
        charge_pack = pack and not waive_packing_fee
        # 预点单：仅打包单支持预约取餐时段（未来 10 分钟 ~ 2 天内）
        scheduled_dt: datetime | None = None
        raw_scheduled = (scheduled_pickup_at or "").strip()
        if raw_scheduled and dtype == "TAKEAWAY":
            try:
                scheduled_dt = datetime.fromisoformat(raw_scheduled)
            except ValueError:
                raise AppException(40061, "预约取餐时间格式不正确")
            if scheduled_dt.tzinfo is not None:
                scheduled_dt = scheduled_dt.replace(tzinfo=None)
            now = now_local()
            if now.tzinfo is not None:
                now = now.replace(tzinfo=None)
            if scheduled_dt < now + timedelta(minutes=10):
                raise AppException(40062, "预约取餐时间至少需晚于当前 10 分钟")
            if scheduled_dt > now + timedelta(days=2):
                raise AppException(40063, "预约取餐时间不能超过 2 天后")
        t_no, t_ph = resolve_table_parts(table_no_raw, channel=channel, dining_type=dtype)
        store = await db.get(Store, int(store_id)) if store_id else None
        if not store or int(getattr(store, "status", 0) or 0) != 1:
            raise AppException(40011, "门店不存在或已停业")
        sno = store.store_no if store else None

        order_kwargs = dict(
            order_no=generate_no(BIZ_OT, sno, t_no, table_placeholder=t_ph),
            customer_id=customer_id,
            table_id=table_id,
            store_id=store_id,
            status="PENDING_PAYMENT",
            payment_status="UNPAID",
            channel=channel,
            dining_type=dtype,
            remark=remark,
            idempotency_key=scoped_key,
            ordered_at=now_local(),
        )
        if scheduled_dt is not None:
            order_kwargs["scheduled_pickup_at"] = scheduled_dt
        # 关联当前 ACTIVE 就餐会话（过期则不挂旧桌）
        if customer_id and table_id and hasattr(Order, "dining_session_id"):
            from app.services.dining_session_service import DiningSessionService

            active = await DiningSessionService.get_active(db, int(customer_id))
            if active and int(active.table_id) == int(table_id):
                order_kwargs["dining_session_id"] = active.id
                await DiningSessionService.touch(db, active.id)
            elif table_token:
                from app.services.table_resolve import resolve_dining_table

                tbl = await resolve_dining_table(db, table_token, store_id=store_id)
                if tbl:
                    sess = await DiningSessionService.start_or_refresh(
                        db,
                        customer_id=int(customer_id),
                        table=tbl,
                        table_token=table_token,
                    )
                    order_kwargs["dining_session_id"] = sess.id
        # operator_id 列在迁移后存在；旧库无该字段时忽略
        if hasattr(Order, "operator_id") and operator_id is not None:
            order_kwargs["operator_id"] = operator_id
        order = Order(**order_kwargs)
        db.add(order)
        try:
            await db.flush()
        except IntegrityError:
            # 并发幂等：唯一约束冲突后返回已有订单
            await db.rollback()
            if scoped_key:
                existing = await db.execute(select(Order).where(Order.idempotency_key == scoped_key))
                order = existing.scalar_one_or_none()
                if order:
                    OrderCreateService._assert_idempotent_order_match(
                        order,
                        customer_id=customer_id,
                        store_id=store_id,
                        channel=channel,
                        operator_id=operator_id,
                    )
                    if not OrderCreateService._is_reusable_idempotent_order(order):
                        raise AppException(40903, "该幂等键对应订单已结束，请重新下单")
                    return order
            raise AppException(40901, "订单创建冲突，请重试")

        subtotal, packing_fee, discountable_subtotal = await OrderPriceCalculator.build_order_items(
            db, order, items, deduct_stock=True, need_pack=charge_pack
        )
        social_applied = False
        if skip_promotions:
            # 余额支付：按原价（含餐盒），不叠会员折扣/券/代金券/公益
            discount_amount = money(0)
            coupon_discount = money(0)
            voucher_discount = money(0)
            coupon_id = None
            voucher_id = None
            total_amount = money(subtotal + packing_fee)
        else:
            from app.services.coupon_service import CouponService
            from app.services.social_benefit_service import SocialBenefitService
            from app.models import Product

            # 公益优先（互斥）：显式 use_social_benefit 或传入 social_rule_id
            social_hit = None
            if customer_id and (use_social_benefit or social_rule_id):
                if customer_coupon_id or customer_voucher_id:
                    raise AppException(40008, "公益折扣不可与优惠券同享，请取消券后重试")
                pids = [int(i["product_id"]) for i in items]
                from app.models import Product as _Product

                prods = (
                    await db.execute(select(_Product).where(_Product.id.in_(pids)))
                ).scalars().all() if pids else []
                cids = [int(p.category_id) for p in prods if p.category_id]
                if social_rule_id:
                    from app.models import SocialBenefitRule

                    rule = await db.get(SocialBenefitRule, int(social_rule_id))
                    codes = await SocialBenefitService.active_codes(db, int(customer_id))
                    if (
                        rule
                        and int(rule.status) == 1
                        and rule.identity_code in codes
                        and rule.benefit_type == "ORDER_DISCOUNT"
                        and rule.discount_rate is not None
                        and (rule.store_id is None or int(rule.store_id) == int(order.store_id))
                    ):
                        social_hit = (rule, rule.identity_code)
                if social_hit is None and use_social_benefit:
                    social_hit = await SocialBenefitService.find_best_rule(
                        db,
                        customer_id=int(customer_id),
                        store_id=int(order.store_id),
                        product_ids=pids,
                        category_ids=cids,
                    )

            if social_hit:
                rule, identity_code = social_hit
                rate = money(rule.discount_rate)
                special = money(subtotal - discountable_subtotal)
                if special < 0:
                    special = money(0)
                subsidy = money(discountable_subtotal * (Decimal("1") - rate))
                if rule.max_subsidy_amount is not None:
                    subsidy = min(subsidy, money(rule.max_subsidy_amount))
                discount_amount = subsidy
                coupon_discount = money(0)
                voucher_discount = money(0)
                coupon_id = None
                voucher_id = None
                total_amount = money(discountable_subtotal - subsidy + special + packing_fee)
                await SocialBenefitService.reserve_for_order(
                    db,
                    order_id=int(order.id),
                    customer_id=int(customer_id),
                    store_id=int(order.store_id),
                    rule=rule,
                    identity_code=identity_code,
                    subsidy_amount=subsidy,
                    operator_user_id=operator_id,
                    meta={
                        "discount_rate": str(rate),
                        "stack_policy": rule.stack_policy,
                        "subtotal": str(subtotal),
                        "discountable_subtotal": str(discountable_subtotal),
                    },
                )
                social_applied = True
            else:
                rate = await CustomerService.resolve_member_discount_rate(db, customer_id)
                # PERCENT 与会员折互斥：券力度严格更优时跳过会员折，由折扣券独占（取最优）
                hold_tmpl = await CouponService.get_hold_template(db, customer_coupon_id, customer_id)
                effective_rate = CouponService.effective_member_rate_with_coupon(rate, hold_tmpl)
                discount_amount, base_total = CustomerService.calc_member_discount(
                    subtotal, packing_fee, effective_rate, discountable_subtotal=discountable_subtotal
                )
                coupon_eligible = CustomerService.calc_coupon_eligible_subtotal(
                    subtotal, discountable_subtotal, discount_amount
                )
                coupon_discount, coupon_id = await CouponService.resolve_discount(
                    db,
                    customer_coupon_id,
                    customer_id,
                    coupon_eligible,
                    order_store_id=int(order.store_id) if getattr(order, "store_id", None) else None,
                    member_rate=rate,
                )
                payable_before_voucher = money(base_total - coupon_discount)
                voucher_eligible = CustomerService.calc_voucher_eligible_payable(
                    coupon_eligible, coupon_discount, packing_fee, need_pack=pack
                )
                voucher_discount, voucher_id = await CouponService.resolve_voucher_discount(
                    db,
                    customer_voucher_id,
                    customer_id,
                    payable_before_voucher,
                    eligible_payable=voucher_eligible,
                    order_store_id=int(order.store_id) if getattr(order, "store_id", None) else None,
                )
                total_amount = money(payable_before_voucher - voucher_discount)
        order.subtotal = subtotal
        order.discount_amount = discount_amount
        order.customer_coupon_id = coupon_id
        order.coupon_discount = coupon_discount
        order.customer_voucher_id = voucher_id
        order.voucher_discount = voucher_discount
        order.packing_fee = packing_fee
        order.total_amount = total_amount
        if coupon_id:
            from app.services.coupon_service import CouponService

            await CouponService.reserve_for_order(
                db, coupon_id, order_id=order.id, customer_id=customer_id
            )
        if voucher_id:
            from app.services.coupon_service import CouponService

            await CouponService.reserve_for_order(
                db, voucher_id, order_id=order.id, customer_id=customer_id
            )
        _ = social_applied  # 台账已在 reserve_for_order 写入 PENDING
        await record_order_status(
            db, order_id=order.id, from_status=None, to_status="PENDING_PAYMENT", source="SYSTEM"
        )
        await db.flush()
        return order

    @staticmethod
    async def update_status(
        db: AsyncSession,
        order_id: int,
        status: str,
        *,
        operator_id: int | None = None,
        force_takeover: bool = False,
    ) -> Order:
        from app.services.kitchen_claim import apply_kitchen_claim_for_status
        from app.services.order_lifecycle import apply_order_status

        order = await db.get(Order, order_id)
        if not order:
            raise AppException(40404, "订单不存在")
        await apply_kitchen_claim_for_status(
            db,
            order,
            status,
            operator_id=operator_id,
            force_takeover=force_takeover,
        )
        order = await apply_order_status(
            db,
            order,
            status,
            source="STAFF",
            operator_id=operator_id,
            assert_kind="STAFF",
        )
        return order


class PreferenceService:
    """智能口味：根据已付订单持续分析并修正偏好。

    每次支付成功（或退款后）按顾客近期已付订单全量重算：
    - 按行数量加权（买 2 份加料计 2 次）
    - 近期订单加权更高（口味改变时自动修正）
    - 午餐/晚餐时段、周末轻微加权；部分退款弱负反馈降权
    - 全额退款订单不再参与分析
    """

    HIGH_PREFERENCE_THRESHOLD = Decimal("0.25")
    MIN_ORDER_COUNT = 2
    # 迎宾 TTS 等场景自行 slice；口味爱好应用须返回全部分项（7 组 + 多项加料可 >8）
    TOP_N = 8
    # 偏好分母：已支付订单（全额退款后 payment_status=REFUNDED 不计入）
    PREFERENCE_SCORE_PAYMENT_STATUSES = ("PAID", "PARTIAL_REFUND")
    # 近期加权：越新权重越高；分析最近 N 单
    RECENCY_DECAY = Decimal("0.85")
    RECENCY_WINDOW = 40
    # 时段场景：午餐/晚餐略加权
    MEAL_HOUR_BOOST = Decimal("1.15")
    # 周末订单轻微加权（休闲场景口味）
    WEEKEND_BOOST = Decimal("1.08")
    # 部分退款：弱负反馈，降低该单规格权重
    PARTIAL_REFUND_WEIGHT = Decimal("0.5")

    @staticmethod
    def _score_quantize(value: Decimal) -> Decimal:
        return min(Decimal("1"), max(Decimal("0"), value.quantize(Decimal("0.0001"))))

    @staticmethod
    def _order_weight(order, *, idx: int, decay: Decimal) -> Decimal:
        weight = (decay ** idx) if decay > 0 else Decimal("1")
        when = order.paid_at or order.created_at
        try:
            hour = int(when.hour) if when is not None else -1
        except Exception:
            hour = -1
        if hour in range(11, 15) or hour in range(17, 22):
            weight *= PreferenceService.MEAL_HOUR_BOOST
        try:
            wd = int(when.weekday()) if when is not None else -1
        except Exception:
            wd = -1
        if wd >= 5:
            weight *= PreferenceService.WEEKEND_BOOST
        if getattr(order, "payment_status", None) == "PARTIAL_REFUND":
            weight *= PreferenceService.PARTIAL_REFUND_WEIGHT
        return weight

    @staticmethod
    async def update_from_order(db: AsyncSession, order_id: int) -> None:
        order = await db.get(Order, order_id)
        # 仅在支付成功当下触发（order_lifecycle 在 target=PAID 时调用）
        if not order or not order.customer_id or order.status != "PAID":
            return
        await PreferenceService.recompute_for_customer(db, order.customer_id)
        customer = await db.get(Customer, order.customer_id)
        if customer:
            customer.last_order_at = now_local()

    @staticmethod
    async def recompute_for_customer(db: AsyncSession, customer_id: int) -> None:
        """根据顾客近期已付订单重算全部口味偏好（分析 + 修正）。"""
        if not customer_id:
            return

        orders = list(
            (
                await db.execute(
                    select(Order)
                    .where(
                        Order.customer_id == customer_id,
                        Order.payment_status.in_(PreferenceService.PREFERENCE_SCORE_PAYMENT_STATUSES),
                    )
                    .order_by(
                        func.coalesce(Order.paid_at, Order.created_at).desc(),
                        Order.id.desc(),
                    )
                    .limit(PreferenceService.RECENCY_WINDOW)
                )
            ).scalars().all()
        )
        if not orders:
            existing = (
                await db.execute(
                    select(CustomerPreference).where(CustomerPreference.customer_id == customer_id)
                )
            ).scalars().all()
            for pref in existing:
                await db.delete(pref)
            return

        order_ids = [o.id for o in orders]
        items = (
            await db.execute(select(OrderItem).where(OrderItem.order_id.in_(order_ids)))
        ).scalars().all()
        item_ids = [i.id for i in items]
        opts_by_item: dict[int, list] = {}
        if item_ids:
            opts = (
                await db.execute(select(OrderItemOption).where(OrderItemOption.order_item_id.in_(item_ids)))
            ).scalars().all()
            for opt in opts:
                opts_by_item.setdefault(opt.order_item_id, []).append(opt)

        items_by_order: dict[int, list] = {}
        for it in items:
            items_by_order.setdefault(it.order_id, []).append(it)

        agg: dict[tuple[str, str], dict] = {}
        total_weight = Decimal("0")
        decay = PreferenceService.RECENCY_DECAY

        for idx, order in enumerate(orders):
            weight = PreferenceService._order_weight(order, idx=idx, decay=decay)
            when = order.paid_at or order.created_at
            order_last = when or now_local()
            for it in items_by_order.get(order.id, []):
                qty = max(1, int(getattr(it, "quantity", None) or 1))
                line_w = weight * Decimal(qty)
                for opt in opts_by_item.get(it.id, []):
                    category = str(opt.group_name_snapshot or "").strip() or "规格"
                    code = str(opt.option_code or "").strip()
                    if not code:
                        continue
                    total_weight += line_w
                    key = (category, code)
                    name = opt.option_name_snapshot or code
                    row = agg.get(key)
                    if not row:
                        agg[key] = {
                            "category": category,
                            "option_code": code,
                            "option_name": name,
                            "order_count": qty,
                            "weighted": line_w,
                            "last_used_at": order_last,
                        }
                    else:
                        row["order_count"] += qty
                        row["weighted"] += line_w
                        if name:
                            row["option_name"] = name
                        if row["last_used_at"] is None or (
                            order_last and order_last > row["last_used_at"]
                        ):
                            row["last_used_at"] = order_last

        if total_weight <= 0:
            total_weight = Decimal("1")

        existing = {
            (p.category, p.option_code): p
            for p in (
                await db.execute(
                    select(CustomerPreference).where(CustomerPreference.customer_id == customer_id)
                )
            ).scalars().all()
        }

        keep_keys: set[tuple[str, str]] = set()
        for key, data in agg.items():
            keep_keys.add(key)
            score = PreferenceService._score_quantize(Decimal(data["weighted"]) / total_weight)
            pref = existing.get(key)
            if pref:
                pref.order_count = int(data["order_count"])
                pref.preference_score = score
                pref.option_name = data["option_name"]
                pref.last_used_at = data["last_used_at"] or now_local()
            else:
                db.add(
                    CustomerPreference(
                        customer_id=customer_id,
                        category=data["category"],
                        option_code=data["option_code"],
                        option_name=data["option_name"],
                        order_count=int(data["order_count"]),
                        preference_score=score,
                        last_used_at=data["last_used_at"] or now_local(),
                    )
                )

        for key, pref in existing.items():
            if key not in keep_keys:
                await db.delete(pref)

    @staticmethod
    async def get_recommendations(
        db: AsyncSession,
        customer_id: int,
        *,
        store_id: int | None = None,
        apply_scene: bool = True,
    ) -> dict:
        prefs_result = await db.execute(
            select(CustomerPreference)
            .where(
                CustomerPreference.customer_id == customer_id,
                CustomerPreference.order_count >= PreferenceService.MIN_ORDER_COUNT,
            )
            .order_by(CustomerPreference.preference_score.desc(), CustomerPreference.order_count.desc())
        )
        prefs = list(prefs_result.scalars().all())
        # 若无达到阈值的，退回全部（首单也能出推荐）
        if not prefs:
            prefs = list(
                (
                    await db.execute(
                        select(CustomerPreference)
                        .where(CustomerPreference.customer_id == customer_id)
                        .order_by(CustomerPreference.preference_score.desc())
                    )
                ).scalars().all()
            )
        high = [p for p in prefs if p.preference_score >= PreferenceService.HIGH_PREFERENCE_THRESHOLD]
        use = high or prefs

        scene: dict = {}
        scene_on = False
        if apply_scene:
            try:
                from app.services.system_settings_service import SystemSettingsService

                scene_on = await SystemSettingsService.effective_bool(
                    db, "preference_scene_enabled", True, store_id=store_id
                )
            except Exception:
                scene_on = True
        if scene_on:
            try:
                from app.services.preference_scene import apply_scene_multiplier, build_scene_context

                scene = await build_scene_context(db, store_id)
            except Exception:
                scene = {}
                scene_on = False

        items: list[dict] = []
        for p in use:
            base = float(p.preference_score)
            score = base
            if scene_on and scene:
                from app.services.preference_scene import apply_scene_multiplier

                score = apply_scene_multiplier(
                    base,
                    option_code=str(p.option_code or ""),
                    last_used_at=getattr(p, "last_used_at", None),
                    season=str(scene.get("season") or "spring"),
                    weather_condition=scene.get("condition"),
                )
            items.append(
                {
                    "category": p.category,
                    "option_code": p.option_code,
                    "option_name": p.option_name or p.option_code,
                    "score": score,
                    "base_score": base,
                    "order_count": p.order_count,
                    "label": f"{p.category}·{(p.option_name or p.option_code)}"
                    if p.category
                    else str(p.option_name or p.option_code),
                }
            )
        items.sort(key=lambda x: (-float(x["score"]), -int(x.get("order_count") or 0)))
        return {
            "preferences": items,
            "scene": {
                "season": scene.get("season"),
                "condition": scene.get("condition"),
                "temp_c": scene.get("temp_c"),
                "enabled": scene_on,
            }
            if scene_on
            else {"enabled": False},
        }

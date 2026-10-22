from decimal import Decimal

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.biz_no import BIZ_CU, generate_no
from app.core.exceptions import AppException
from app.core.response import money, now_local, serialize_dt
from app.models import (
    Customer,
    CustomerAlipayAccount,
    CustomerCoupon,
    CustomerIdentity,
    CustomerWechatAccount,
    DiningTable,
    MemberLevel,
    Order,
    PointExchangeOption,
    PointLedger,
    Store,
)

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
    async def find_active_by_phone(db: AsyncSession, phone: str) -> Customer | None:
        """按完整手机号找启用顾客（不限门店）；已验证优先。"""
        phone = (phone or "").strip()
        if not phone:
            return None
        return (
            await db.execute(
                select(Customer)
                .where(Customer.status == 1, Customer.phone == phone)
                .order_by(Customer.phone_verified.desc(), Customer.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def remember_pos_phone(
        db: AsyncSession,
        phone: str,
        *,
        store_id: int,
    ) -> tuple[Customer, bool]:
        """收银预留手机号：已有则复用；没有则建 GUEST 壳账号（未验证）。

        顾客日后小程序官方授权同一手机号时，会把本壳上的积分/订单/公益身份并入扫码账号。
        """
        import re

        phone = (phone or "").strip()
        if not re.fullmatch(r"1\d{10}", phone):
            raise AppException(40053, "手机号格式不正确")
        existing = await CustomerService.find_active_by_phone(db, phone)
        if existing is not None:
            if not getattr(existing, "registered_store_id", None):
                existing.registered_store_id = int(store_id)
                await db.flush()
            return existing, False
        customer = CustomerService._new_guest_customer()
        customer.phone = phone
        customer.phone_verified = 0
        customer.display_name = f"收银预留·{phone[-4:]}"
        customer.nickname = customer.display_name
        customer.registered_store_id = int(store_id)
        customer.profile_status = "PARTIAL"
        db.add(customer)
        await db.flush()
        return customer, True

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
        import re

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
        # 完整手机号在本店口径下无命中时，再按全局精确手机号兜底（便于选中「收银预留」壳）
        if not rows and re.fullmatch(r"1\d{10}", kw):
            exact = await CustomerService.find_active_by_phone(db, kw)
            if exact is not None:
                return [exact], 1
        return rows, int(total)

    @staticmethod
    def is_member(customer: Customer) -> bool:
        return str(getattr(customer, "member_status", "") or "").upper() == "MEMBER"

    @staticmethod
    def identity_name_payload(customer: Customer) -> dict:
        """展示名 / 昵称原样返回。禁止用 nickname 顶替空 display_name，避免编辑/双栏展示串值。"""
        return {
            "display_name": customer.display_name,
            "nickname": customer.nickname,
        }

    @staticmethod
    def names_equal(left: str | None, right: str | None) -> bool:
        return str(left or "").strip() == str(right or "").strip()

    @staticmethod
    def staff_customer_name_fields(customer: Customer | None) -> dict:
        """后台列表/详情统一拆分顾客名，禁止把昵称塞进展示名。

        - customer_display_name：员工维护的展示名（可空）
        - customer_nickname：渠道/小程序镜像昵称（可空）
        - customer_name：兼容旧前端的单字段称呼，仅作「展示名优先、否则昵称」兜底
        """
        if not customer:
            return {
                "customer_display_name": None,
                "customer_nickname": None,
                "customer_name": None,
            }
        display = (customer.display_name or "").strip() or None
        nick = (customer.nickname or "").strip() or None
        return {
            "customer_display_name": display,
            "customer_nickname": nick,
            "customer_name": display or nick,
        }

    @staticmethod
    def apply_miniprogram_nickname(customer: Customer, nickname: str | None) -> bool:
        """小程序改昵称：写 customers.nickname；不覆盖员工已定制的展示名。

        展示名仅在以下情况跟随昵称更新：
        1) 展示名为空；或
        2) 展示名仍等于改名前的昵称（当初由昵称自动拷贝而来）。

        返回是否实际改动了 nickname / display_name。
        """
        if not CustomerService.is_meaningful_nickname(nickname):
            return False
        n = str(nickname).strip()
        old_nick = (customer.nickname or "").strip() or None
        old_display = (customer.display_name or "").strip() or None
        changed = False
        if not CustomerService.names_equal(old_nick, n):
            customer.nickname = n
            changed = True
        if not CustomerService.is_meaningful_nickname(old_display):
            if not CustomerService.names_equal(customer.display_name, n):
                customer.display_name = n
                changed = True
        elif old_nick and CustomerService.names_equal(old_display, old_nick):
            if not CustomerService.names_equal(old_display, n):
                customer.display_name = n
                changed = True
        return changed

    @staticmethod
    def public_label(customer: Customer | None) -> str | None:
        """单字段称呼兜底：展示名优先，否则昵称。新接口请用 staff_customer_name_fields。"""
        if not customer:
            return None
        name = (customer.display_name or customer.nickname or "").strip()
        return name or None

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
        """支付成功：实付 1 元 = 1 积分，并尝试升级等级。行锁 + point_ledger。

        outbox 可能晚于退款：全额退/取消不再发分；若已有成功退款则发分后按 refund 补扣。
        """
        if not order or not getattr(order, "id", None):
            return 0
        # 锁订单再判态，避免与退款状态机竞态
        locked_order = (
            await db.execute(select(Order).where(Order.id == int(order.id)).with_for_update())
        ).scalar_one_or_none()
        if not locked_order or not locked_order.customer_id:
            return 0
        order = locked_order
        st = (order.status or "").upper()
        pay = (order.payment_status or "").upper()
        if st in ("CANCELLED", "REFUNDED") or pay in ("REFUNDED", "CANCELLED", "UNPAID"):
            return 0
        if pay not in ("PAID", "PARTIAL_REFUND") and st not in (
            "PAID",
            "PREPARING",
            "READY",
            "COMPLETED",
            "PARTIAL_REFUND",
        ):
            return 0

        customer = (
            await db.execute(
                select(Customer).where(Customer.id == int(order.customer_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if not customer:
            return 0
        # 同订单幂等：已有 EARN 流水则仍尝试补扣已成功退款（幂等）
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
        earned = 0
        if not existed:
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

        # 迟到 award：对已 SUCCESS 的退款补扣（按 refund_id 幂等）
        from app.models import PaymentRefund

        refunds = list(
            (
                await db.execute(
                    select(PaymentRefund).where(
                        PaymentRefund.order_id == int(order.id),
                        PaymentRefund.status == "SUCCESS",
                    )
                )
            )
            .scalars()
            .all()
        )
        for rf in refunds:
            await CustomerService.deduct_points_for_refund(
                db, order, rf.amount, refund_id=int(rf.id)
            )
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
    async def exchange_points_for_coupon(
        db: AsyncSession,
        customer_id: int,
        option_id: int,
    ) -> dict:
        """积分换券：扣减积分 → 调 issue_by_template_id 发券 → 写 EXCHANGE 流水。

        幂等靠发券侧 client_request_id（PTS:{cid}:{oid}:{YYYYMM}），
        同月同选项重复请求不扣分、不重复发券。
        """
        from app.services.coupon_service import CouponService

        customer = (
            await db.execute(
                select(Customer).where(Customer.id == int(customer_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if not customer:
            raise AppException(40404, "顾客不存在")
        option = await db.get(PointExchangeOption, int(option_id))
        if not option or int(option.status) != 1:
            raise AppException(40404, "兑换选项不存在或已停用")
        cost = int(option.points_cost)
        if int(customer.points or 0) < cost:
            raise AppException(40010, "积分不足")

        # 幂等键：同月同选项只发一次
        month_key = now_local().strftime("%Y%m")
        req_key = f"PTS:{int(customer.id)}:{int(option.id)}:{month_key}"
        # 先查是否已发过（issue_by_template_id 内部也查，但提前查可避免不必要的扣分）
        existing_coupon = (
            await db.execute(
                select(CustomerCoupon).where(CustomerCoupon.client_request_id == req_key)
            )
        ).scalar_one_or_none()
        if existing_coupon:
            return {
                "already_exchanged": True,
                "deducted_points": 0,
                "coupon_id": int(existing_coupon.id),
                "remaining_points": int(customer.points or 0),
            }

        # 发券（原子扣库存 + PTS 幂等键）；顾客行 FOR UPDATE 保证同客串行，防重扣分
        coupons = await CouponService.issue_by_template_id(
            db,
            int(customer.id),
            int(option.template_id),
            quantity=1,
            client_request_id=req_key,
            issued_at_store_id=int(customer.registered_store_id)
            if customer.registered_store_id
            else None,
        )
        if not coupons:
            raise AppException(50020, "发券失败，请重试")
        coupon = coupons[0]

        # 扣减积分（行锁已持有 customer）
        before = int(customer.points or 0)
        customer.points = before - cost
        db.add(
            PointLedger(
                customer_id=customer.id,
                order_id=None,
                refund_id=None,
                delta=-cost,
                balance_after=int(customer.points),
                entry_type="EXCHANGE",
            )
        )
        await CustomerService.recalculate_member_level(db, customer)
        await db.flush()
        return {
            "already_exchanged": False,
            "deducted_points": cost,
            "coupon_id": int(coupon.id),
            "remaining_points": int(customer.points),
        }

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
        """按可打折餐品小计计算会员优惠（打包费、特价餐品不参与折扣）。返回 (discount_amount, total_amount)。"""
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
        """会员折后可用于优惠券的餐品小计（不含特价餐品）。"""
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
        """可用于代金券门槛/面额判断的基数（不含特价餐品小计，可选含打包费）。"""
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
        """用渠道资料补空字段：昵称与展示名各自判断，互不顶替已有值。"""
        if CustomerService.is_meaningful_nickname(nickname):
            n = str(nickname).strip()
            if not CustomerService.is_meaningful_nickname(customer.nickname):
                customer.nickname = n
            if not CustomerService.is_meaningful_nickname(customer.display_name):
                customer.display_name = n
        if avatar_url and not str(customer.avatar_url or "").strip():
            customer.avatar_url = str(avatar_url).strip()

    @staticmethod
    async def sync_nickname_to_channel_accounts(
        db: AsyncSession,
        customer: Customer,
        *,
        nickname: str | None = None,
    ) -> None:
        """仅当后台改了「昵称」时同步到微信/支付宝渠道账号。

        展示名是员工侧字段，不推送到小程序渠道，避免覆盖两端各自昵称。
        """
        n = str(nickname if nickname is not None else (customer.nickname or "") or "").strip()
        if not CustomerService.is_meaningful_nickname(n):
            return
        for ch in ("wechat", "alipay"):
            await CustomerService.save_channel_profile(db, int(customer.id), ch, nickname=n)

    @staticmethod
    async def sync_display_to_channel_accounts(
        db: AsyncSession,
        customer: Customer,
        *,
        nickname: str | None = None,
    ) -> None:
        """兼容旧名：语义等同 sync_nickname_to_channel_accounts（只同步昵称）。"""
        await CustomerService.sync_nickname_to_channel_accounts(
            db, customer, nickname=nickname
        )

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
            # 渠道昵称只回落顾客表 nickname，禁止把员工展示名抄进小程序渠道
            fallback_nick = str(customer.nickname or "").strip()
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
                    "该已验证手机号已关联其他账号，确认后可合并历史订单、积分、优惠券与公益身份",
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

        # 收银预留壳 / 其它同号未验证账号 → 并入当前官方绑号账号（积分、订单、公益身份）
        phone_peers = list(
            (
                await db.execute(
                    select(Customer)
                    .where(
                        Customer.status == 1,
                        Customer.phone == phone,
                        Customer.id != int(customer.id),
                    )
                    .with_for_update()
                )
            ).scalars().all()
        )
        if phone_peers:
            from app.services.customer_merge_service import CustomerMergeService

            for peer in phone_peers:
                has_platform = await CustomerService._customer_has_platform_login(db, int(peer.id))
                if has_platform and not confirm_merge:
                    raise AppException(
                        40906,
                        "该手机号在收银侧已有关联账号，确认后可合并积分、订单、优惠券与公益身份",
                        status_code=409,
                    )
                await CustomerMergeService.merge(
                    db,
                    target_customer_id=int(customer.id),
                    source_customer_id=int(peer.id),
                    reason=f"官方{source}绑号合并收银预留/同号账号",
                    request=request,
                )
                merged = True
            customer = await db.get(Customer, int(customer.id))
            if not customer:
                raise AppException(40905, "账号合并失败，请重试", status_code=409)
            customer.phone = phone
            customer.phone_verified = 1
            CustomerService.refresh_profile_status(customer)
            await db.flush()

        return customer, merged

    @staticmethod
    async def _customer_has_platform_login(db: AsyncSession, customer_id: int) -> bool:
        """是否已有微信/支付宝登录身份（非纯收银预留壳）。"""
        wx = await db.scalar(
            select(CustomerWechatAccount.id)
            .where(CustomerWechatAccount.customer_id == int(customer_id))
            .limit(1)
        )
        if wx:
            return True
        ali = await db.scalar(
            select(CustomerAlipayAccount.id)
            .where(CustomerAlipayAccount.customer_id == int(customer_id))
            .limit(1)
        )
        return bool(ali)

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


def __getattr__(name: str):
    """下单与口味偏好已拆到独立模块，原导入路径保持可用。"""
    if name == "OrderCreateService":
        from app.services.order_create_service import OrderCreateService

        return OrderCreateService
    if name == "PreferenceService":
        from app.services.preference_service import PreferenceService

        return PreferenceService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

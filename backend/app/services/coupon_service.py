"""优惠券：发券、校验、计价、核销。"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.biz_no import generate_no
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.orm_safe import orm_attr_loaded
from app.core.response import ensure_local_aware, money, now_local, naive_local_now, serialize_dt
from app.models import CouponTemplate, CustomerCoupon, Order
from app.services.customer_service import CustomerService

logger = logging.getLogger(__name__)


class CouponService:
    @staticmethod
    async def welcome_template_name(
        db: AsyncSession | None = None,
        store_id: int | None = None,
    ) -> str:
        """注册赠券模板名：门店 store_settings 优先，否则全局，再否则 .env。"""
        name = (get_settings().welcome_coupon_name or "").strip()
        if db is not None:
            from app.services.system_settings_service import SystemSettingsService

            name = (
                await SystemSettingsService.effective_setting(
                    db, "welcome_coupon_name", store_id=store_id, fallback=name
                )
            ).strip()
        return name or "新客满20减3"

    @staticmethod
    async def _welcome_template(
        db: AsyncSession,
        welcome_name: str,
        store_id: int | None = None,
    ) -> CouponTemplate | None:
        """同名模板时优先本店，其次总部（store_id 为空）。"""
        rows = list(
            (
                await db.execute(
                    select(CouponTemplate).where(
                        CouponTemplate.name == welcome_name,
                        CouponTemplate.status == 1,
                    )
                )
            ).scalars().all()
        )
        if not rows:
            return None
        if store_id:
            owned = [r for r in rows if r.store_id and int(r.store_id) == int(store_id)]
            if owned:
                return owned[0]
        hq = [r for r in rows if r.store_id is None]
        if hq:
            return hq[0]
        return rows[0]

    @staticmethod
    async def hq_name_override_warning(
        db: AsyncSession, *, store_id: int | None, name: str
    ) -> str | None:
        """门店模板与总部启用同名时提示将覆盖欢迎/同名解析（不阻断）。"""
        if store_id is None:
            return None
        nm = (name or "").strip()
        if not nm:
            return None
        from app.services.scope_override import hq_override_warning

        hq = (
            await db.execute(
                select(CouponTemplate)
                .where(
                    CouponTemplate.store_id.is_(None),
                    CouponTemplate.name == nm,
                    CouponTemplate.status == 1,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if not hq:
            return None
        return hq_override_warning(
            key_label="券模板名",
            key_desc=f"名称「{nm}」",
            hq_name=hq.name,
        )
    @staticmethod
    def discount_label(tmpl: CouponTemplate) -> str:
        if tmpl.coupon_type == "PERCENT":
            rate = money(tmpl.discount_value)
            zhe = (rate * Decimal("10")).quantize(Decimal("0.1"))
            zhe_s = format(zhe.normalize(), "f").rstrip("0").rstrip(".")
            return f"{zhe_s}折"
        if tmpl.coupon_type == "VOUCHER":
            return f"¥{money(tmpl.discount_value)}代金券"
        return f"减¥{money(tmpl.discount_value)}"

    @staticmethod
    def summary_label(tmpl: CouponTemplate) -> str:
        base = CouponService.discount_label(tmpl)
        min_amt = money(tmpl.min_order_amount or 0)
        if tmpl.coupon_type == "VOUCHER":
            if min_amt > 0:
                return f"满{min_amt}可用{base}"
            return base
        if min_amt > 0:
            return f"满{min_amt}{base}"
        return base

    @staticmethod
    def is_voucher_type(coupon_type: str | None) -> bool:
        return (coupon_type or "").upper() == "VOUCHER"

    @staticmethod
    def is_percent_type(coupon_type: str | None) -> bool:
        return (coupon_type or "").upper() == "PERCENT"

    @staticmethod
    def percent_beats_member(coupon_rate: Decimal, member_rate: Decimal) -> bool:
        """折扣券力度是否严格优于会员折（rate 更小=折扣更深）。同等或更弱不可用。"""
        cr = money(coupon_rate)
        mr = money(member_rate)
        if cr <= Decimal("0") or cr >= Decimal("1"):
            return False
        if mr <= Decimal("0") or mr > Decimal("1"):
            mr = Decimal("1")
        return cr < mr

    @staticmethod
    def effective_member_rate_with_coupon(member_rate: Decimal, tmpl: CouponTemplate | None) -> Decimal:
        """PERCENT 券严格更优时跳过会员折（由折扣券独占）；否则保留会员折。"""
        rate = money(member_rate)
        if rate <= Decimal("0") or rate > Decimal("1"):
            rate = Decimal("1")
        if not tmpl or not CouponService.is_percent_type(tmpl.coupon_type):
            return rate
        if CouponService.percent_beats_member(money(tmpl.discount_value), rate):
            return Decimal("1")
        return rate

    @staticmethod
    async def get_hold_template(
        db: AsyncSession,
        customer_coupon_id: int | None,
        customer_id: int | None,
    ) -> CouponTemplate | None:
        """读取持券对应模板（不计可用性）；无券或不存在返回 None。"""
        if not customer_coupon_id or not customer_id:
            return None
        row = (
            await db.execute(
                select(CustomerCoupon, CouponTemplate)
                .join(CouponTemplate, CouponTemplate.id == CustomerCoupon.template_id)
                .where(CustomerCoupon.id == int(customer_coupon_id))
            )
        ).first()
        if not row:
            return None
        cc, tmpl = row
        if int(cc.customer_id) != int(customer_id):
            return None
        return tmpl

    @staticmethod
    def template_payload(tmpl: CouponTemplate) -> dict:
        sid = getattr(tmpl, "store_id", None)
        return {
            "id": tmpl.id,
            "store_id": int(sid) if sid else None,
            "store_scope": "HQ" if not sid else "STORE",
            "name": tmpl.name,
            "coupon_type": tmpl.coupon_type,
            "discount_value": str(tmpl.discount_value),
            "min_order_amount": str(tmpl.min_order_amount),
            "valid_days": int(tmpl.valid_days or 30),
            "status": int(tmpl.status or 0),
            "total_quota": getattr(tmpl, "total_quota", None),
            "remaining": getattr(tmpl, "remaining", None),
            "store_whitelist": getattr(tmpl, "store_whitelist", None),
            "discount_label": CouponService.discount_label(tmpl),
            "summary": CouponService.summary_label(tmpl),
            # CouponTemplate 无 created_at 列；勿 getattr 读 mapped 时间戳
            "created_at": None,
        }

    @staticmethod
    def template_visible_to_store(tmpl: CouponTemplate, store_id: int | None) -> bool:
        """store_id=None（超管全店）可见全部；否则可见本店模板 + 白名单内的总部模板。"""
        if store_id is None:
            return True
        sid = getattr(tmpl, "store_id", None)
        if sid is None:
            # P2-4 总部模板：白名单为 NULL（或规范化后等价的空列表）表示全部门店可见；非空则仅列出门店可见
            whitelist = getattr(tmpl, "store_whitelist", None)
            if not whitelist:
                return True
            try:
                return int(store_id) in {int(s) for s in whitelist}
            except (TypeError, ValueError):
                return False
        return int(sid) == int(store_id)

    @staticmethod
    async def list_templates(
        db: AsyncSession,
        include_disabled: bool = True,
        *,
        store_id: int | None = None,
    ) -> list[dict]:
        q = select(CouponTemplate).order_by(CouponTemplate.id.desc())
        if not include_disabled:
            q = q.where(CouponTemplate.status == 1)
        if store_id is not None:
            q = q.where(
                or_(
                    CouponTemplate.store_id.is_(None),
                    CouponTemplate.store_id == int(store_id),
                )
            )
        rows = list((await db.execute(q)).scalars().all())
        # P2-4 总部模板白名单过滤（Python 层，兼容 MySQL/SQLite）
        if store_id is not None:
            rows = [t for t in rows if CouponService.template_visible_to_store(t, store_id)]
        store_ids = {int(t.store_id) for t in rows if getattr(t, "store_id", None)}
        name_map: dict[int, str] = {}
        if store_ids:
            from app.models import Store

            stores = list(
                (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars().all()
            )
            name_map = {int(s.id): s.name for s in stores}
        out = []
        for t in rows:
            payload = CouponService.template_payload(t)
            sid = payload.get("store_id")
            if sid:
                payload["store_name"] = name_map.get(int(sid))
            out.append(payload)
        return out

    @staticmethod
    async def create_template(db: AsyncSession, data: dict) -> CouponTemplate:
        CouponService._validate_template_data(data)
        payload = dict(data)
        if payload.get("discount_value") is not None:
            payload["discount_value"] = money(payload["discount_value"])
        if payload.get("min_order_amount") is not None:
            payload["min_order_amount"] = money(payload["min_order_amount"])
        if "store_id" in payload and payload["store_id"] is not None:
            payload["store_id"] = int(payload["store_id"])
        # P0-6: 创建模板时初始化 remaining = total_quota（NULL=不限；0=禁止发放）
        quota = payload.get("total_quota")
        if quota is not None:
            quota = int(quota)
            if quota >= 1:
                payload["total_quota"] = quota
                payload["remaining"] = quota
            else:
                # quota=0：显式保持上限 0 并设 remaining=0 禁止发放
                payload["total_quota"] = 0
                payload["remaining"] = 0
        else:
            payload.pop("total_quota", None)
            payload.pop("remaining", None)
        # P2-4 白名单仅对总部模板生效；门店模板强制为 None
        if payload.get("store_id") is not None:
            payload["store_whitelist"] = None
        elif "store_whitelist" in payload:
            payload["store_whitelist"] = CouponService._normalize_whitelist(payload.get("store_whitelist"))
        tmpl = CouponTemplate(**payload)
        db.add(tmpl)
        await db.flush()
        return tmpl

    @staticmethod
    async def update_template(db: AsyncSession, tmpl: CouponTemplate, data: dict) -> CouponTemplate:
        merged = {
            "name": data.get("name", tmpl.name),
            "coupon_type": data.get("coupon_type", tmpl.coupon_type),
            "discount_value": data.get("discount_value", tmpl.discount_value),
            "min_order_amount": data.get("min_order_amount", tmpl.min_order_amount),
            "valid_days": data.get("valid_days", tmpl.valid_days),
            "status": data.get("status", tmpl.status),
        }
        CouponService._validate_template_data(merged)
        for key, val in data.items():
            if key == "store_id":
                tmpl.store_id = int(val) if val is not None else None
                # P2-4 模板改为门店归属后白名单失效，改回总部后需重新配置
                if tmpl.store_id is not None:
                    tmpl.store_whitelist = None
            elif key == "store_whitelist":
                # 仅总部模板可设白名单；显式 None=恢复全部门店可见
                if tmpl.store_id is None:
                    tmpl.store_whitelist = CouponService._normalize_whitelist(val)
            elif key == "total_quota":
                # P0-6: 仅修改 total_quota 上限，不影响 remaining
                if val is not None:
                    tmpl.total_quota = int(val)
                else:
                    tmpl.total_quota = None
            elif key == "remaining":
                # remaining 不允许直接通过 API 修改
                continue
            elif key == "discount_value" and val is not None:
                tmpl.discount_value = money(val)
            elif key == "min_order_amount" and val is not None:
                tmpl.min_order_amount = money(val)
            elif val is not None:
                setattr(tmpl, key, val)
        await db.flush()
        return tmpl

    @staticmethod
    def _normalize_whitelist(value) -> list[int] | None:
        """P2-4 规范化门店白名单：None/空列表=全部门店（None）；否则返回去重后的正整数列表。"""
        if value is None:
            return None
        try:
            ids = {int(s) for s in value if int(s) > 0}
        except (TypeError, ValueError):
            raise AppException(40069, "门店白名单必须是门店 ID 数组")
        return sorted(ids) if ids else None

    @staticmethod
    def _validate_template_data(data: dict) -> None:
        ctype = (data.get("coupon_type") or "FIXED").upper()
        val = money(data.get("discount_value") or 0)
        if ctype == "PERCENT":
            if val <= Decimal("0") or val >= Decimal("1"):
                raise AppException(40067, "折扣券比例须在 0~1 之间（如 0.9 表示 9 折）")
        elif val <= Decimal("0"):
            raise AppException(40068, "满减/代金券面额须大于 0")

    @staticmethod
    async def issue_by_template_id(
        db: AsyncSession,
        customer_id: int,
        template_id: int,
        quantity: int = 1,
        *,
        client_request_id: str | None = None,
        issued_by_user_id: int | None = None,
        issued_at_store_id: int | None = None,
    ) -> list[CustomerCoupon]:
        from sqlalchemy import update as sa_update
        from sqlalchemy.exc import IntegrityError

        from app.models import Customer

        customer = await db.get(Customer, int(customer_id))
        if not customer or int(customer.status) != 1:
            raise AppException(40406, "顾客不存在或已停用")
        tmpl = await db.get(CouponTemplate, int(template_id))
        if not tmpl or int(tmpl.status) != 1:
            raise AppException(40407, "优惠券模板不存在或已停用")

        is_voucher = CouponService.is_voucher_type(tmpl.coupon_type)
        qty = max(1, min(int(quantity or 1), 50))
        prefix = "VP" if is_voucher else "CP"
        expires = now_local() + timedelta(days=int(tmpl.valid_days or 30))

        # P0-6: 客户端幂等键 — 同 key 重复请求返回已有行
        req_key = (client_request_id or "").strip()[:128] if client_request_id else None
        if req_key:
            existing = (
                await db.execute(
                    select(CustomerCoupon).where(CustomerCoupon.client_request_id == req_key)
                )
            ).scalars().all()
            if existing:
                return list(existing)

        # P0-6: 原子扣减库存（remaining IS NULL = 不限量，跳过扣减）
        remaining = getattr(tmpl, "remaining", None)
        if remaining is not None:
            result = await db.execute(
                sa_update(CouponTemplate)
                .where(
                    CouponTemplate.id == tmpl.id,
                    CouponTemplate.remaining.isnot(None),
                    CouponTemplate.remaining >= qty,
                )
                .values(remaining=CouponTemplate.remaining - qty)
            )
            if not result.rowcount:
                raise AppException(40070, "优惠券库存不足")

        # 发券门店：优先使用参数传入；若模板本身绑定门店，继承模板门店
        effective_store_id = issued_at_store_id
        if effective_store_id is None and getattr(tmpl, "store_id", None) is not None:
            effective_store_id = int(tmpl.store_id)

        issued: list[CustomerCoupon] = []
        try:
            async with db.begin_nested():
                for _ in range(qty):
                    cc = CustomerCoupon(
                        customer_id=customer.id,
                        template_id=tmpl.id,
                        coupon_no=generate_no(prefix),
                        status="AVAILABLE",
                        expires_at=expires,
                        client_request_id=req_key if req_key else None,
                        issued_by_user_id=int(issued_by_user_id) if issued_by_user_id else None,
                        issued_at_store_id=int(effective_store_id) if effective_store_id else None,
                    )
                    db.add(cc)
                    issued.append(cc)
                await db.flush()
        except IntegrityError as exc:
            msg = str(getattr(exc, "orig", exc)).upper()
            if "CLIENT_REQUEST_ID" in msg or ("UNIQUE" in msg and req_key):
                # 幂等键冲突：回查已发行记录
                await db.rollback()
                existing = (
                    await db.execute(
                        select(CustomerCoupon).where(CustomerCoupon.client_request_id == req_key)
                    )
                ).scalars().all()
                if existing:
                    return list(existing)
                raise AppException(50020, "发券冲突，请重试") from exc
            if "UNIQUE" in msg or "DUPLICATE" in msg:
                # coupon_no 冲突：若已扣库存则需回补
                if remaining is not None:
                    await db.execute(
                        sa_update(CouponTemplate)
                        .where(CouponTemplate.id == tmpl.id)
                        .values(remaining=CouponTemplate.remaining + qty)
                    )
                raise AppException(50020, "发券冲突，请重试") from exc
            raise
        return issued

    @staticmethod
    async def list_customer_coupons_admin(
        db: AsyncSession,
        *,
        customer_id: int | None = None,
        template_id: int | None = None,
        status: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
        store_id: int | None = None,
        issued_store_id: int | None = None,
        coupon_type: str | None = None,
    ) -> tuple[list[dict], int]:
        from sqlalchemy import func, or_

        from app.models import Customer, Order

        q = (
            select(CustomerCoupon, CouponTemplate, Customer)
            .join(CouponTemplate, CouponTemplate.id == CustomerCoupon.template_id)
            .join(Customer, Customer.id == CustomerCoupon.customer_id)
        )
        if store_id is not None:
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
        if customer_id:
            q = q.where(CustomerCoupon.customer_id == int(customer_id))
        if template_id:
            q = q.where(CustomerCoupon.template_id == int(template_id))
        # 发券门店是业务筛选（券实际从哪个店发出），与上方 store_id 的数据隔离口径不同
        if issued_store_id is not None:
            if int(issued_store_id) > 0:
                q = q.where(CustomerCoupon.issued_at_store_id == int(issued_store_id))
            else:
                q = q.where(CustomerCoupon.issued_at_store_id.is_(None))
        if coupon_type and coupon_type.strip():
            q = q.where(CouponTemplate.coupon_type == coupon_type.strip().upper())
        if status:
            q = q.where(CustomerCoupon.status == status.strip().upper())
        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            q = q.where(
                or_(
                    CustomerCoupon.coupon_no.like(kw),
                    Customer.customer_no.like(kw),
                    Customer.phone.like(kw),
                    Customer.nickname.like(kw),
                    Customer.display_name.like(kw),
                )
            )
        total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        rows = list(
            (
                await db.execute(
                    # 批量发券会产生大量 obtained_at 完全相同的行，必须用 id 兜底防翻页重复/丢行
                    q.order_by(CustomerCoupon.obtained_at.desc(), CustomerCoupon.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
        items = []
        for cc, tmpl, cust in rows:
            items.append(
                {
                    "id": cc.id,
                    "coupon_no": cc.coupon_no,
                    "customer_id": cust.id,
                    "customer_no": cust.customer_no,
                    **CustomerService.staff_customer_name_fields(cust),
                    "customer_phone": cust.phone,
                    "template_id": tmpl.id,
                    "template_name": tmpl.name,
                    "coupon_type": tmpl.coupon_type,
                    "summary": CouponService.summary_label(tmpl),
                    "status": cc.status,
                    "obtained_at": serialize_dt(cc.obtained_at) if cc.obtained_at else None,
                    "expires_at": serialize_dt(cc.expires_at) if cc.expires_at else None,
                    "used_order_id": cc.used_order_id,
                    "used_at": serialize_dt(cc.used_at) if cc.used_at else None,
                    "issued_by_user_id": int(cc.issued_by_user_id) if getattr(cc, "issued_by_user_id", None) else None,
                    "issued_at_store_id": int(cc.issued_at_store_id) if getattr(cc, "issued_at_store_id", None) else None,
                    "store_restricted": bool(getattr(cc, "issued_at_store_id", None)),
                }
            )
        issuer_ids = list({int(it["issued_by_user_id"]) for it in items if it.get("issued_by_user_id")})
        issuer_map: dict[int, str] = {}
        if issuer_ids:
            from app.models import User

            issuer_map = {
                u.id: (u.display_name or u.username)
                for u in (await db.execute(select(User).where(User.id.in_(issuer_ids)))).scalars().all()
            }
        for it in items:
            uid = it.get("issued_by_user_id")
            # 扫码/注册/里程碑等系统自动发券：无发券员工时统一展示「系统发放」
            it["issued_by_name"] = issuer_map.get(int(uid)) if uid else "系统发放"
        return items, int(total)

    @staticmethod
    def _hold_payload(cc: CustomerCoupon, tmpl: CouponTemplate) -> dict:
        issued_store_id = getattr(cc, "issued_at_store_id", None)
        return {
            "id": cc.id,
            "coupon_no": cc.coupon_no,
            "name": tmpl.name,
            "template_name": tmpl.name,
            "coupon_type": tmpl.coupon_type,
            "kind": "VOUCHER" if CouponService.is_voucher_type(tmpl.coupon_type) else "COUPON",
            "discount_value": str(tmpl.discount_value),
            "min_order_amount": str(tmpl.min_order_amount),
            "discount_label": CouponService.discount_label(tmpl),
            "summary": CouponService.summary_label(tmpl),
            "expires_at": serialize_dt(cc.expires_at) if cc.expires_at else None,
            "issued_at_store_id": int(issued_store_id) if issued_store_id else None,
            "store_restricted": bool(issued_store_id),
        }

    @staticmethod
    async def _list_available_filtered(
        db: AsyncSession, customer_id: int, *, voucher_only: bool, store_id: int | None = None
    ) -> list[dict]:
        now = naive_local_now()
        q = (
            select(CustomerCoupon, CouponTemplate)
            .join(CouponTemplate, CouponTemplate.id == CustomerCoupon.template_id)
            .where(
                CustomerCoupon.customer_id == customer_id,
                CustomerCoupon.status == "AVAILABLE",
                CustomerCoupon.expires_at > now,
                CouponTemplate.status == 1,
            )
            .order_by(CustomerCoupon.expires_at.asc())
        )
        rows = list((await db.execute(q)).all())
        items = []
        for cc, tmpl in rows:
            is_voucher = CouponService.is_voucher_type(tmpl.coupon_type)
            if voucher_only and not is_voucher:
                continue
            if not voucher_only and is_voucher:
                continue
            # 门店隔离：有 issued_at_store_id 的券仅在本店可见
            if store_id is not None:
                issued_store = getattr(cc, "issued_at_store_id", None)
                if issued_store is not None and int(issued_store) != int(store_id):
                    continue
            items.append(CouponService._hold_payload(cc, tmpl))
        return items

    @staticmethod
    async def list_available(db: AsyncSession, customer_id: int, *, store_id: int | None = None) -> list[dict]:
        return await CouponService._list_available_filtered(db, customer_id, voucher_only=False, store_id=store_id)

    @staticmethod
    async def list_available_vouchers(db: AsyncSession, customer_id: int, *, store_id: int | None = None) -> list[dict]:
        return await CouponService._list_available_filtered(db, customer_id, voucher_only=True, store_id=store_id)

    @staticmethod
    async def issue_from_template(
        db: AsyncSession,
        customer_id: int,
        template_name: str,
        store_id: int | None = None,
    ) -> CustomerCoupon | None:
        from sqlalchemy.exc import IntegrityError

        tmpl = await CouponService._welcome_template(db, template_name, store_id=store_id)
        if not tmpl:
            return None

        # P0-6: 用 client_request_id 做 DB UNIQUE 幂等，取代 TOCTOU SELECT-then-INSERT
        req_key = f"WELCOME:{int(customer_id)}:{int(tmpl.id)}"
        existing = (
            await db.execute(
                select(CustomerCoupon).where(CustomerCoupon.client_request_id == req_key)
            )
        ).scalar_one_or_none()
        if existing:
            return None

        # P0-6: 原子扣减库存（remaining IS NULL = 不限量）
        remaining = getattr(tmpl, "remaining", None)
        if remaining is not None:
            from sqlalchemy import update as sa_update

            result = await db.execute(
                sa_update(CouponTemplate)
                .where(
                    CouponTemplate.id == tmpl.id,
                    CouponTemplate.remaining.isnot(None),
                    CouponTemplate.remaining >= 1,
                )
                .values(remaining=CouponTemplate.remaining - 1)
            )
            if not result.rowcount:
                return None

        # 发券门店粘性：扫码开卡店；无则继承模板门店
        effective_store_id = int(store_id) if store_id else None
        if effective_store_id is None and getattr(tmpl, "store_id", None) is not None:
            effective_store_id = int(tmpl.store_id)

        try:
            async with db.begin_nested():
                cc = CustomerCoupon(
                    customer_id=customer_id,
                    template_id=tmpl.id,
                    coupon_no=generate_no("CP"),
                    status="AVAILABLE",
                    expires_at=now_local() + timedelta(days=int(tmpl.valid_days or 30)),
                    client_request_id=req_key,
                    issued_by_user_id=None,  # 系统发放
                    issued_at_store_id=effective_store_id,
                )
                db.add(cc)
                await db.flush()
                return cc
        except IntegrityError:
            # 幂等键冲突：另一并发请求已成功发券
            if remaining is not None:
                from sqlalchemy import update as sa_update

                await db.execute(
                    sa_update(CouponTemplate)
                    .where(CouponTemplate.id == tmpl.id)
                    .values(remaining=CouponTemplate.remaining + 1)
                )
            return None

    @staticmethod
    async def _welcome_bundle_template_ids(
        db: AsyncSession, store_id: int | None = None
    ) -> list[int]:
        """解析 welcome_bundle_template_ids（JSON 数组）；非法则空列表。"""
        import json

        from app.services.system_settings_service import SystemSettingsService

        raw = (
            await SystemSettingsService.effective_setting(
                db, "welcome_bundle_template_ids", store_id=store_id, fallback=""
            )
        ).strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("welcome_bundle_template_ids JSON 无效: %r", raw)
            return []
        if not isinstance(data, list):
            return []
        out: list[int] = []
        for x in data:
            try:
                tid = int(x)
            except (TypeError, ValueError):
                continue
            if tid > 0:
                out.append(tid)
        return out

    @staticmethod
    async def issue_welcome_coupon(
        db: AsyncSession,
        customer_id: int,
        store_id: int | None = None,
    ) -> CustomerCoupon | None:
        """注册会员赠送新客券。

        优先 welcome_bundle_template_ids（多券幂等 welcome_pack:{cid}:{tid}:v1）；
        否则回退 welcome_coupon_name 单券逻辑。
        返回最后成功发放的一张（兼容旧调用方）。
        """
        bundle_ids = await CouponService._welcome_bundle_template_ids(db, store_id=store_id)
        if bundle_ids:
            last: CustomerCoupon | None = None
            for tid in bundle_ids:
                req = f"welcome_pack:{int(customer_id)}:{int(tid)}:v1"
                try:
                    issued_list = await CouponService.issue_by_template_id(
                        db,
                        customer_id=int(customer_id),
                        template_id=int(tid),
                        quantity=1,
                        client_request_id=req,
                        issued_at_store_id=int(store_id) if store_id else None,
                    )
                except AppException as exc:
                    logger.warning(
                        "欢迎礼包跳过 template_id=%s customer=%s: %s",
                        tid,
                        customer_id,
                        getattr(exc, "message", exc),
                    )
                    continue
                if issued_list:
                    last = issued_list[-1]
            return last

        welcome_name = await CouponService.welcome_template_name(db, store_id=store_id)
        tmpl = await CouponService._welcome_template(db, welcome_name, store_id=store_id)
        if not tmpl:
            logger.warning(
                "欢迎券未发放：模板不存在或未启用 name=%r（请检查 coupon_templates 与 .env WELCOME_COUPON_NAME）",
                welcome_name,
            )
            return None
        exists = await db.scalar(
            select(CustomerCoupon.id)
            .where(CustomerCoupon.customer_id == customer_id, CustomerCoupon.template_id == tmpl.id)
            .limit(1)
        )
        if exists:
            return None
        return await CouponService.issue_from_template(
            db, customer_id, welcome_name, store_id=store_id
        )

    @staticmethod
    async def birthday_coupon_template_name(
        db: AsyncSession | None = None,
        store_id: int | None = None,
    ) -> str:
        name = ""
        if db is not None:
            from app.services.system_settings_service import SystemSettingsService

            name = (
                await SystemSettingsService.effective_setting(
                    db, "birthday_coupon_name", store_id=store_id, fallback=""
                )
            ).strip()
        return name

    @staticmethod
    async def issue_birthday_coupon(
        db: AsyncSession,
        customer_id: int,
        store_id: int | None = None,
        *,
        year: int | None = None,
    ) -> CustomerCoupon | None:
        """生日赠券：每年每模板最多一张；发券人为系统（issued_by_user_id 空）。"""
        from sqlalchemy.exc import IntegrityError

        coupon_name = await CouponService.birthday_coupon_template_name(db, store_id=store_id)
        if not coupon_name:
            return None
        tmpl = await CouponService._welcome_template(db, coupon_name, store_id=store_id)
        if not tmpl:
            logger.warning("生日券未发放：模板不存在或未启用 name=%r", coupon_name)
            return None
        y = int(year or now_local().year)
        req_key = f"BIRTHDAY:{int(customer_id)}:{y}:{int(tmpl.id)}"
        existing = (
            await db.execute(
                select(CustomerCoupon).where(CustomerCoupon.client_request_id == req_key)
            )
        ).scalar_one_or_none()
        if existing:
            return None

        remaining = getattr(tmpl, "remaining", None)
        if remaining is not None:
            from sqlalchemy import update as sa_update

            result = await db.execute(
                sa_update(CouponTemplate)
                .where(
                    CouponTemplate.id == tmpl.id,
                    CouponTemplate.remaining.isnot(None),
                    CouponTemplate.remaining >= 1,
                )
                .values(remaining=CouponTemplate.remaining - 1)
            )
            if not result.rowcount:
                return None

        effective_store_id = int(store_id) if store_id else None
        if effective_store_id is None and getattr(tmpl, "store_id", None) is not None:
            effective_store_id = int(tmpl.store_id)

        try:
            async with db.begin_nested():
                cc = CustomerCoupon(
                    customer_id=customer_id,
                    template_id=tmpl.id,
                    coupon_no=generate_no("CP"),
                    status="AVAILABLE",
                    expires_at=now_local() + timedelta(days=int(tmpl.valid_days or 30)),
                    client_request_id=req_key,
                    issued_by_user_id=None,
                    issued_at_store_id=effective_store_id,
                )
                db.add(cc)
                await db.flush()
                return cc
        except IntegrityError:
            if remaining is not None:
                from sqlalchemy import update as sa_update

                await db.execute(
                    sa_update(CouponTemplate)
                    .where(CouponTemplate.id == tmpl.id)
                    .values(remaining=CouponTemplate.remaining + 1)
                )
            return None

    @staticmethod
    async def welcome_coupon_payload(db: AsyncSession, issued: CustomerCoupon | None) -> dict | None:
        if not issued:
            return None
        tmpl = await db.get(CouponTemplate, issued.template_id)
        if not tmpl:
            return None
        return CouponService._hold_payload(issued, tmpl)

    @staticmethod
    async def resolve_discount(
        db: AsyncSession,
        customer_coupon_id: int | None,
        customer_id: int | None,
        eligible_subtotal: Decimal,
        *,
        order_store_id: int | None = None,
        member_rate: Decimal | None = None,
    ) -> tuple[Decimal, int | None]:
        """校验并计算优惠券减免。eligible_subtotal=可用餐品小计（不含特价；PERCENT 取优时为未打会员折基数）。

        PERCENT 与会员折互斥：仅当折扣券力度严格优于会员折时可用；同等或更弱禁用（不可折上折）。
        """
        if not customer_coupon_id or not customer_id:
            return Decimal("0.00"), None
        row = (
            await db.execute(
                select(CustomerCoupon, CouponTemplate)
                .join(CouponTemplate, CouponTemplate.id == CustomerCoupon.template_id)
                .where(CustomerCoupon.id == int(customer_coupon_id))
            )
        ).first()
        if not row:
            raise AppException(40061, "优惠券不存在")
        cc, tmpl = row
        if CouponService.is_voucher_type(tmpl.coupon_type):
            raise AppException(40069, "请使用代金券入口选择该券")
        if int(cc.customer_id) != int(customer_id):
            raise AppException(40062, "无权使用该优惠券")
        if cc.status != "AVAILABLE":
            raise AppException(40063, "优惠券不可用")
        # 发券门店限制：如果券记录了发券门店，订单必须在该门店使用
        issued_store = getattr(cc, "issued_at_store_id", None)
        if issued_store is not None and order_store_id is not None:
            if int(issued_store) != int(order_store_id):
                raise AppException(40060, "该优惠券仅在发券门店可用，不可跨门店使用")
        # 总部模板白名单：无发券门店粘性时，仍须满足模板对当前店可见
        if order_store_id is not None and not CouponService.template_visible_to_store(tmpl, int(order_store_id)):
            raise AppException(40060, "该优惠券不可在当前门店使用")
        now = now_local()
        expires = ensure_local_aware(cc.expires_at)
        if expires and expires <= now:
            cc.status = "EXPIRED"
            await db.flush()
            raise AppException(40064, "优惠券已过期")
        eligible = money(eligible_subtotal)
        min_amt = money(tmpl.min_order_amount or 0)
        if eligible < min_amt:
            raise AppException(40065, f"未满{coupon_service_min_label(min_amt)}，不可用此券")
        if tmpl.coupon_type == "PERCENT":
            rate = money(tmpl.discount_value)
            if rate <= Decimal("0") or rate >= Decimal("1"):
                raise AppException(40066, "优惠券配置异常")
            mr = money(member_rate) if member_rate is not None else Decimal("1")
            if not CouponService.percent_beats_member(rate, mr):
                raise AppException(40077, "折扣券力度未优于会员折扣，不可使用（同等力度取会员折）")
            disc = money(eligible * (Decimal("1") - rate))
        else:
            disc = min(money(tmpl.discount_value), eligible)
        return disc, cc.id

    @staticmethod
    async def resolve_voucher_discount(
        db: AsyncSession,
        customer_voucher_id: int | None,
        customer_id: int | None,
        payable_amount: Decimal,
        *,
        eligible_payable: Decimal | None = None,
        order_store_id: int | None = None,
    ) -> tuple[Decimal, int | None]:
        """校验并计算代金券抵扣。payable_amount=订单应付；eligible_payable=可参与代金券的基数（不含特价餐品）。"""
        if not customer_voucher_id or not customer_id:
            return Decimal("0.00"), None
        row = (
            await db.execute(
                select(CustomerCoupon, CouponTemplate)
                .join(CouponTemplate, CouponTemplate.id == CustomerCoupon.template_id)
                .where(CustomerCoupon.id == int(customer_voucher_id))
            )
        ).first()
        if not row:
            raise AppException(40071, "代金券不存在")
        cc, tmpl = row
        if not CouponService.is_voucher_type(tmpl.coupon_type):
            raise AppException(40072, "请使用优惠券入口选择该券")
        if int(cc.customer_id) != int(customer_id):
            raise AppException(40062, "无权使用该代金券")
        if cc.status != "AVAILABLE":
            raise AppException(40073, "代金券不可用")
        # 发券门店限制
        issued_store = getattr(cc, "issued_at_store_id", None)
        if issued_store is not None and order_store_id is not None:
            if int(issued_store) != int(order_store_id):
                raise AppException(40060, "该代金券仅在发券门店可用，不可跨门店使用")
        if order_store_id is not None and not CouponService.template_visible_to_store(tmpl, int(order_store_id)):
            raise AppException(40060, "该代金券不可在当前门店使用")
        now = now_local()
        expires = ensure_local_aware(cc.expires_at)
        if expires and expires <= now:
            cc.status = "EXPIRED"
            await db.flush()
            raise AppException(40074, "代金券已过期")
        eligible = money(eligible_payable if eligible_payable is not None else payable_amount)
        min_amt = money(tmpl.min_order_amount or 0)
        if eligible < min_amt:
            raise AppException(40075, f"应付未满{coupon_service_min_label(min_amt)}，不可用此代金券")
        face = money(tmpl.discount_value)
        # 代金券一次性核销（支付后整券 USED），不支持找零/余额结转。
        # 可参与代金券的基数不足面额时不使用，避免浪费。
        if eligible < face:
            return Decimal("0.00"), None
        disc = min(face, eligible)
        return disc, cc.id

    @staticmethod
    async def reserve_for_order(
        db: AsyncSession,
        coupon_id: int | None,
        *,
        order_id: int,
        customer_id: int | None,
    ) -> None:
        """下单后预占券，防并发双花。先锁行再二次校验 status/expires_at/customer_id。"""
        if not coupon_id or not customer_id:
            return
        row = (
            await db.execute(
                select(CustomerCoupon)
                .where(CustomerCoupon.id == int(coupon_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not row:
            raise AppException(40063, "优惠券不可用或已被占用")
        if int(row.customer_id) != int(customer_id):
            raise AppException(40062, "无权使用该优惠券")
        if row.status != "AVAILABLE":
            raise AppException(40063, "优惠券不可用或已被占用")
        expires = ensure_local_aware(getattr(row, "expires_at", None))
        if expires and expires <= now_local():
            row.status = "EXPIRED"
            await db.flush()
            raise AppException(40064, "优惠券已过期")
        row.status = "RESERVED"
        row.used_order_id = order_id
        await db.flush()

    @staticmethod
    async def release_for_order(db: AsyncSession, order: Order) -> None:
        """关单/取消时释放预占券；全额退款时回滚已核销券（行锁防与 mark_used 竞态）。

        - RESERVED：未支付取消 → 恢复 AVAILABLE
        - USED：已支付后退款至 REFUNDED → 恢复 AVAILABLE（仅本订单核销的券）
        幂等：已 AVAILABLE 或 used_order_id 不匹配则跳过。
        同时按 used_order_id 反查，避免 order 上 FK 丢失导致漏释放。
        """
        ids: set[int] = set()
        for cid in (order.customer_coupon_id, order.customer_voucher_id):
            if cid:
                ids.add(int(cid))
        extra = (
            await db.execute(
                select(CustomerCoupon.id).where(CustomerCoupon.used_order_id == int(order.id))
            )
        ).scalars().all()
        ids.update(int(x) for x in extra if x)

        for cid in ids:
            cc = (
                await db.execute(
                    select(CustomerCoupon).where(CustomerCoupon.id == int(cid)).with_for_update()
                )
            ).scalar_one_or_none()
            if not cc:
                continue
            if int(cc.used_order_id or 0) != int(order.id):
                continue
            if cc.status in ("RESERVED", "USED"):
                cc.status = "AVAILABLE"
                cc.used_order_id = None
                cc.used_at = None
        await db.flush()

    @staticmethod
    async def mark_used(db: AsyncSession, order: Order) -> None:
        """支付成功核销：仅 RESERVED 且挂在本单的券可转 USED（行锁防竞态）。

        幂等：
        - 已对本单 USED → 跳过
        - 订单已退款/取消，或券已被 release 回 AVAILABLE → 跳过（不抛错，避免 outbox 打爆）
        - 仍挂在本单的 RESERVED（含幂等脏数据）→ 落到 USED
        """
        st = (order.status or "").strip().upper()
        ps = (order.payment_status or "").strip().upper()
        if st in ("REFUNDED", "CANCELLED") or ps == "REFUNDED":
            return

        for attr in ("customer_coupon_id", "customer_voucher_id"):
            cid = getattr(order, attr, None)
            if not cid:
                continue
            cc = (
                await db.execute(
                    select(CustomerCoupon).where(CustomerCoupon.id == int(cid)).with_for_update()
                )
            ).scalar_one_or_none()
            if not cc:
                continue
            if cc.status == "USED" and int(cc.used_order_id or 0) == int(order.id):
                continue
            # 退款竞态：release 已把券还回 AVAILABLE
            if cc.status == "AVAILABLE":
                continue
            if cc.status != "RESERVED" or int(cc.used_order_id or 0) != int(order.id):
                logger.warning(
                    "优惠券无法核销（跳过）order_id=%s coupon_id=%s status=%s used_order_id=%s",
                    order.id,
                    cc.id,
                    cc.status,
                    cc.used_order_id,
                )
                continue
            cc.status = "USED"
            cc.used_order_id = order.id
            cc.used_at = now_local()
        await db.flush()

    @staticmethod
    async def reconcile_for_order(db: AsyncSession, order: Order) -> None:
        """按订单终态对账券：已付→USED；已退/取消→AVAILABLE。供 outbox 补偿调用。"""
        st = (order.status or "").strip().upper()
        ps = (order.payment_status or "").strip().upper()
        if st in ("REFUNDED", "CANCELLED") or ps == "REFUNDED":
            await CouponService.release_for_order(db, order)
            return
        if ps in ("PAID", "PARTIAL_REFUND", "PARTIAL_PAID") or st in (
            "PAID",
            "CONFIRMED",
            "PREPARING",
            "COOKING",
            "READY",
            "COMPLETED",
        ):
            await CouponService.mark_used(db, order)

    # ---------- 累计成单里程碑自动发券 ----------

    @staticmethod
    async def count_paid_orders_at_store(
        db: AsyncSession,
        customer_id: int,
        store_id: int,
    ) -> int:
        """本店累计成功支付订单数（含部分退款；不含未付/全额退/已取消）。"""
        from sqlalchemy import func

        q = (
            select(func.count())
            .select_from(Order)
            .where(
                Order.customer_id == int(customer_id),
                Order.store_id == int(store_id),
                Order.paid_at.isnot(None),
                Order.payment_status.in_(("PAID", "PARTIAL_REFUND")),
                Order.status != "CANCELLED",
            )
        )
        return int(await db.scalar(q) or 0)

    @staticmethod
    def milestone_payload(row, tmpl: CouponTemplate | None = None) -> dict:
        from app.models import OrderMilestoneReward

        assert isinstance(row, OrderMilestoneReward) or hasattr(row, "id")
        out = {
            "id": int(row.id),
            "store_id": int(row.store_id),
            "threshold_orders": int(row.threshold_orders),
            "template_id": int(row.template_id),
            "status": int(row.status or 0),
            "created_at": (
                serialize_dt(row.created_at) if orm_attr_loaded(row, "created_at") else None
            ),
            "updated_at": (
                serialize_dt(row.updated_at) if orm_attr_loaded(row, "updated_at") else None
            ),
        }
        if tmpl is not None:
            out["template_name"] = tmpl.name
            out["template_summary"] = CouponService.summary_label(tmpl)
            out["coupon_type"] = tmpl.coupon_type
        return out

    @staticmethod
    async def list_milestone_rewards(
        db: AsyncSession,
        store_id: int,
        *,
        include_disabled: bool = True,
    ) -> list[dict]:
        from app.models import OrderMilestoneReward

        q = (
            select(OrderMilestoneReward)
            .where(OrderMilestoneReward.store_id == int(store_id))
            .order_by(OrderMilestoneReward.threshold_orders.asc())
        )
        if not include_disabled:
            q = q.where(OrderMilestoneReward.status == 1)
        rows = list((await db.execute(q)).scalars().all())
        tmpl_ids = {int(r.template_id) for r in rows}
        tmpl_map: dict[int, CouponTemplate] = {}
        if tmpl_ids:
            tmpls = list(
                (await db.execute(select(CouponTemplate).where(CouponTemplate.id.in_(tmpl_ids)))).scalars().all()
            )
            tmpl_map = {int(t.id): t for t in tmpls}
        return [
            CouponService.milestone_payload(r, tmpl_map.get(int(r.template_id)))
            for r in rows
        ]

    @staticmethod
    async def replace_milestone_rewards(
        db: AsyncSession,
        store_id: int,
        items: list[dict],
    ) -> list[dict]:
        """整表替换本店里程碑规则（按 threshold 去重；空列表=清空）。"""
        from app.models import OrderMilestoneReward

        sid = int(store_id)
        cleaned: list[dict] = []
        seen: set[int] = set()
        for raw in items or []:
            thr = int(raw.get("threshold_orders") or 0)
            tid = int(raw.get("template_id") or 0)
            st = int(raw.get("status") if raw.get("status") is not None else 1)
            if thr < 1 or thr > 100000:
                raise AppException(40076, "里程碑单数须在 1~100000")
            if tid < 1:
                raise AppException(40076, "请选择券模板")
            if thr in seen:
                raise AppException(40076, f"重复的里程碑单数：{thr}")
            seen.add(thr)
            tmpl = await db.get(CouponTemplate, tid)
            if not tmpl or int(tmpl.status) != 1:
                raise AppException(40407, f"券模板不存在或已停用（id={tid}）")
            if not CouponService.template_visible_to_store(tmpl, sid):
                raise AppException(40301, f"券模板不可用于本门店：{tmpl.name}")
            cleaned.append({"threshold_orders": thr, "template_id": tid, "status": 1 if st else 0})

        existing = list(
            (
                await db.execute(
                    select(OrderMilestoneReward).where(OrderMilestoneReward.store_id == sid)
                )
            ).scalars().all()
        )
        for row in existing:
            await db.delete(row)
        await db.flush()

        for item in cleaned:
            db.add(
                OrderMilestoneReward(
                    store_id=sid,
                    threshold_orders=item["threshold_orders"],
                    template_id=item["template_id"],
                    status=item["status"],
                )
            )
        await db.flush()
        return await CouponService.list_milestone_rewards(db, sid, include_disabled=True)

    @staticmethod
    async def issue_milestones_after_paid(db: AsyncSession, order: Order) -> list[CustomerCoupon]:
        """支付成功后：按本店累计成单数，对已达阈值且未发过的规则自动发券。

        幂等键 MILESTONE:{customer_id}:{store_id}:{threshold}，同档只发一次。
        """
        from app.models import OrderMilestoneReward

        cid = getattr(order, "customer_id", None)
        sid = getattr(order, "store_id", None)
        if not cid or not sid:
            return []
        count = await CouponService.count_paid_orders_at_store(db, int(cid), int(sid))
        rules = list(
            (
                await db.execute(
                    select(OrderMilestoneReward)
                    .where(
                        OrderMilestoneReward.store_id == int(sid),
                        OrderMilestoneReward.status == 1,
                        OrderMilestoneReward.threshold_orders <= int(count),
                    )
                    .order_by(OrderMilestoneReward.threshold_orders.asc())
                )
            ).scalars().all()
        )
        issued_all: list[CustomerCoupon] = []
        for rule in rules:
            req_key = CouponService.milestone_client_request_id(
                int(cid), int(sid), int(rule.threshold_orders)
            )
            try:
                issued = await CouponService.issue_by_template_id(
                    db,
                    int(cid),
                    int(rule.template_id),
                    quantity=1,
                    client_request_id=req_key,
                    issued_by_user_id=None,
                    issued_at_store_id=int(sid),
                )
                issued_all.extend(issued)
            except AppException as exc:
                logger.warning(
                    "里程碑发券跳过 customer=%s store=%s threshold=%s template=%s code=%s msg=%s",
                    cid,
                    sid,
                    rule.threshold_orders,
                    rule.template_id,
                    getattr(exc, "code", None),
                    getattr(exc, "message", str(exc)),
                )
            except Exception:
                logger.exception(
                    "里程碑发券异常 customer=%s store=%s threshold=%s",
                    cid,
                    sid,
                    rule.threshold_orders,
                )
        return issued_all

    @staticmethod
    def milestone_client_request_id(customer_id: int, store_id: int, threshold: int) -> str:
        return f"MILESTONE:{int(customer_id)}:{int(store_id)}:{int(threshold)}"

    @staticmethod
    def parse_milestone_threshold(client_request_id: str | None) -> int | None:
        """从幂等键解析档位；已回收键（含 :REVOKED:）仍返回原 threshold。"""
        key = (client_request_id or "").strip()
        if not key.startswith("MILESTONE:"):
            return None
        parts = key.split(":")
        if len(parts) < 4:
            return None
        try:
            return int(parts[3])
        except (TypeError, ValueError):
            return None

    @staticmethod
    async def reclaim_milestones_after_full_refund(
        db: AsyncSession,
        order: Order,
        *,
        refund_id: int | None = None,
    ) -> list[int]:
        """全额退款后：计单下降导致未达标的档位，作废未使用里程碑券并释放幂等键以便再达档可重发。

        - 仅处理 AVAILABLE 券；RESERVED/USED 已占用权益，不作废（记日志）
        - 幂等：已是 EXPIRED + status_reason=milestone_reclaim 则跳过
        """
        cid = getattr(order, "customer_id", None)
        sid = getattr(order, "store_id", None)
        if not cid or not sid:
            return []
        ps = (getattr(order, "payment_status", None) or "").strip().upper()
        st = (getattr(order, "status", None) or "").strip().upper()
        if ps != "REFUNDED" and st != "REFUNDED":
            return []

        count = await CouponService.count_paid_orders_at_store(db, int(cid), int(sid))
        prefix = f"MILESTONE:{int(cid)}:{int(sid)}:"
        rows = list(
            (
                await db.execute(
                    select(CustomerCoupon)
                    .where(
                        CustomerCoupon.customer_id == int(cid),
                        CustomerCoupon.client_request_id.isnot(None),
                        CustomerCoupon.client_request_id.like(f"{prefix}%"),
                    )
                    .with_for_update()
                )
            ).scalars().all()
        )
        revoked: list[int] = []
        for cc in rows:
            thr = CouponService.parse_milestone_threshold(cc.client_request_id)
            if thr is None or thr <= int(count):
                continue
            reason = (getattr(cc, "status_reason", None) or "").strip()
            if cc.status == "EXPIRED" and reason == "milestone_reclaim":
                continue
            if cc.status in ("RESERVED", "USED"):
                logger.info(
                    "里程碑券已占用不回收 coupon_id=%s status=%s order_id=%s threshold=%s",
                    cc.id,
                    cc.status,
                    order.id,
                    thr,
                )
                continue
            if cc.status != "AVAILABLE":
                continue
            old_key = (cc.client_request_id or "").strip()
            # 释放原幂等键，允许再次达档后重发
            suffix = f":REVOKED:{int(refund_id or order.id)}"
            cc.client_request_id = (old_key + suffix)[:128]
            cc.status = "EXPIRED"
            cc.status_reason = "milestone_reclaim"
            revoked.append(int(cc.id))
        if revoked:
            await db.flush()
            logger.info(
                "里程碑券已回收 customer=%s store=%s count=%s coupon_ids=%s",
                cid,
                sid,
                count,
                revoked,
            )
        return revoked


def coupon_service_min_label(min_amt: Decimal) -> str:
    return f"¥{money(min_amt)}"

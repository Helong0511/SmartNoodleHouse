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
from app.core.response import ensure_local_aware, money, now_local, naive_local_now
from app.models import CouponTemplate, CustomerCoupon, Order

logger = logging.getLogger(__name__)


class CouponService:
    @staticmethod
    async def welcome_template_name(db: AsyncSession | None = None) -> str:
        """注册赠券模板名：优先 system_settings，否则 .env WELCOME_COUPON_NAME。"""
        name = (get_settings().welcome_coupon_name or "").strip()
        if db is not None:
            from app.services.system_settings_service import SystemSettingsService

            name = (
                await SystemSettingsService.effective_setting(
                    db, "welcome_coupon_name", fallback=name
                )
            ).strip()
        return name or "新客满30减5"
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
            "created_at": tmpl.created_at.isoformat() if getattr(tmpl, "created_at", None) else None,
        }

    @staticmethod
    def template_visible_to_store(tmpl: CouponTemplate, store_id: int | None) -> bool:
        """store_id=None（超管全店）可见全部；否则可见本店模板 + 白名单内的总部模板。"""
        if store_id is None:
            return True
        sid = getattr(tmpl, "store_id", None)
        if sid is None:
            # P2-4 总部模板：白名单为 NULL 或空数组表示全部门店可见；非空则仅列出门店可见
            # 空数组与 _normalize_whitelist 的归一化口径保持一致（[]→None），
            # 兼容历史数据/直接写入的 JSON []
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
                    q.order_by(CustomerCoupon.obtained_at.desc())
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
                    "customer_name": cust.display_name or cust.nickname,
                    "customer_phone": cust.phone,
                    "template_id": tmpl.id,
                    "template_name": tmpl.name,
                    "coupon_type": tmpl.coupon_type,
                    "summary": CouponService.summary_label(tmpl),
                    "status": cc.status,
                    "obtained_at": cc.obtained_at.isoformat() if cc.obtained_at else None,
                    "expires_at": cc.expires_at.isoformat() if cc.expires_at else None,
                    "used_order_id": cc.used_order_id,
                    "used_at": cc.used_at.isoformat() if cc.used_at else None,
                    "issued_by_user_id": int(cc.issued_by_user_id) if getattr(cc, "issued_by_user_id", None) else None,
                    "issued_at_store_id": int(cc.issued_at_store_id) if getattr(cc, "issued_at_store_id", None) else None,
                    "store_restricted": bool(getattr(cc, "issued_at_store_id", None)),
                }
            )
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
            "expires_at": cc.expires_at.isoformat() if cc.expires_at else None,
            "issued_at_store_id": int(issued_store_id) if issued_store_id else None,
            "store_restricted": bool(issued_store_id),
        }

    @staticmethod
    async def _list_available_filtered(
        db: AsyncSession, customer_id: int, *, voucher_only: bool
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
            items.append(CouponService._hold_payload(cc, tmpl))
        return items

    @staticmethod
    async def list_available(db: AsyncSession, customer_id: int) -> list[dict]:
        return await CouponService._list_available_filtered(db, customer_id, voucher_only=False)

    @staticmethod
    async def list_available_vouchers(db: AsyncSession, customer_id: int) -> list[dict]:
        return await CouponService._list_available_filtered(db, customer_id, voucher_only=True)

    @staticmethod
    async def issue_from_template(
        db: AsyncSession, customer_id: int, template_name: str
    ) -> CustomerCoupon | None:
        from sqlalchemy.exc import IntegrityError

        tmpl = (
            await db.execute(
                select(CouponTemplate).where(
                    CouponTemplate.name == template_name, CouponTemplate.status == 1
                )
            )
        ).scalar_one_or_none()
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

        try:
            async with db.begin_nested():
                cc = CustomerCoupon(
                    customer_id=customer_id,
                    template_id=tmpl.id,
                    coupon_no=generate_no("CP"),
                    status="AVAILABLE",
                    expires_at=now_local() + timedelta(days=int(tmpl.valid_days or 30)),
                    client_request_id=req_key,
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
    async def issue_welcome_coupon(db: AsyncSession, customer_id: int) -> CustomerCoupon | None:
        """注册会员赠送新客券（每人限领该欢迎模板一张；模板缺失时打日志并跳过）。"""
        welcome_name = await CouponService.welcome_template_name(db)
        tmpl = (
            await db.execute(
                select(CouponTemplate).where(
                    CouponTemplate.name == welcome_name,
                    CouponTemplate.status == 1,
                    CouponTemplate.store_id.is_(None),
                )
            )
        ).scalar_one_or_none()
        if not tmpl:
            # 兼容历史：尚无总部模板时允许任意同名启用模板
            tmpl = (
                await db.execute(
                    select(CouponTemplate)
                    .where(CouponTemplate.name == welcome_name, CouponTemplate.status == 1)
                    .order_by(CouponTemplate.id.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
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
        return await CouponService.issue_from_template(db, customer_id, welcome_name)

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
    ) -> tuple[Decimal, int | None]:
        """校验并计算优惠券减免。eligible_subtotal=会员折后可用商品小计（不含特价商品）。"""
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
        """校验并计算代金券抵扣。payable_amount=订单应付；eligible_payable=可参与代金券的基数（不含特价商品）。"""
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
        """关单/取消时释放预占券。"""
        for cid in (order.customer_coupon_id, order.customer_voucher_id):
            if not cid:
                continue
            cc = await db.get(CustomerCoupon, int(cid))
            if cc and cc.status == "RESERVED" and cc.used_order_id == order.id:
                cc.status = "AVAILABLE"
                cc.used_order_id = None
                cc.used_at = None
        await db.flush()

    @staticmethod
    async def mark_used(db: AsyncSession, order: Order) -> None:
        if order.customer_coupon_id:
            cc = await db.get(CustomerCoupon, int(order.customer_coupon_id))
            if cc and cc.status in ("AVAILABLE", "RESERVED"):
                cc.status = "USED"
                cc.used_order_id = order.id
                cc.used_at = now_local()
        if order.customer_voucher_id:
            vp = await db.get(CustomerCoupon, int(order.customer_voucher_id))
            if vp and vp.status in ("AVAILABLE", "RESERVED"):
                vp.status = "USED"
                vp.used_order_id = order.id
                vp.used_at = now_local()
        await db.flush()


def coupon_service_min_label(min_amt: Decimal) -> str:
    return f"¥{money(min_amt)}"

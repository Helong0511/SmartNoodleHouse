"""优惠券：发券、校验、计价、核销。"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.biz_no import generate_no
from app.core.exceptions import AppException
from app.core.response import ensure_local_aware, money, now_local, naive_local_now
from app.models import CouponTemplate, CustomerCoupon, Order

WELCOME_COUPON_NAME = "新客满30减5"


class CouponService:
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
        return {
            "id": tmpl.id,
            "name": tmpl.name,
            "coupon_type": tmpl.coupon_type,
            "discount_value": str(tmpl.discount_value),
            "min_order_amount": str(tmpl.min_order_amount),
            "valid_days": int(tmpl.valid_days or 30),
            "status": int(tmpl.status or 0),
            "discount_label": CouponService.discount_label(tmpl),
            "summary": CouponService.summary_label(tmpl),
            "created_at": tmpl.created_at.isoformat() if getattr(tmpl, "created_at", None) else None,
        }

    @staticmethod
    async def list_templates(db: AsyncSession, include_disabled: bool = True) -> list[dict]:
        q = select(CouponTemplate).order_by(CouponTemplate.id.desc())
        if not include_disabled:
            q = q.where(CouponTemplate.status == 1)
        rows = list((await db.execute(q)).scalars().all())
        return [CouponService.template_payload(t) for t in rows]

    @staticmethod
    async def create_template(db: AsyncSession, data: dict) -> CouponTemplate:
        CouponService._validate_template_data(data)
        tmpl = CouponTemplate(**data)
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
            if val is not None:
                setattr(tmpl, key, val)
        await db.flush()
        return tmpl

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
        db: AsyncSession, customer_id: int, template_id: int, quantity: int = 1
    ) -> list[CustomerCoupon]:
        from app.models import Customer

        customer = await db.get(Customer, int(customer_id))
        if not customer or int(customer.status) != 1:
            raise AppException(40406, "顾客不存在或已停用")
        tmpl = await db.get(CouponTemplate, int(template_id))
        if not tmpl or int(tmpl.status) != 1:
            raise AppException(40407, "优惠券模板不存在或已停用")
        qty = max(1, min(int(quantity or 1), 50))
        issued: list[CustomerCoupon] = []
        for _ in range(qty):
            cc = CustomerCoupon(
                customer_id=customer.id,
                template_id=tmpl.id,
                coupon_no=generate_no("VP" if CouponService.is_voucher_type(tmpl.coupon_type) else "CP"),
                status="AVAILABLE",
                expires_at=now_local() + timedelta(days=int(tmpl.valid_days or 30)),
            )
            db.add(cc)
            issued.append(cc)
        await db.flush()
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
    ) -> tuple[list[dict], int]:
        from sqlalchemy import func, or_

        from app.models import Customer

        q = (
            select(CustomerCoupon, CouponTemplate, Customer)
            .join(CouponTemplate, CouponTemplate.id == CustomerCoupon.template_id)
            .join(Customer, Customer.id == CustomerCoupon.customer_id)
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
                }
            )
        return items, int(total)

    @staticmethod
    def _hold_payload(cc: CustomerCoupon, tmpl: CouponTemplate) -> dict:
        return {
            "id": cc.id,
            "coupon_no": cc.coupon_no,
            "name": tmpl.name,
            "coupon_type": tmpl.coupon_type,
            "kind": "VOUCHER" if CouponService.is_voucher_type(tmpl.coupon_type) else "COUPON",
            "discount_value": str(tmpl.discount_value),
            "min_order_amount": str(tmpl.min_order_amount),
            "discount_label": CouponService.discount_label(tmpl),
            "summary": CouponService.summary_label(tmpl),
            "expires_at": cc.expires_at.isoformat() if cc.expires_at else None,
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
        tmpl = (
            await db.execute(
                select(CouponTemplate).where(
                    CouponTemplate.name == template_name, CouponTemplate.status == 1
                )
            )
        ).scalar_one_or_none()
        if not tmpl:
            return None
        cc = CustomerCoupon(
            customer_id=customer_id,
            template_id=tmpl.id,
            coupon_no=generate_no("CP"),
            status="AVAILABLE",
            expires_at=now_local() + timedelta(days=int(tmpl.valid_days or 30)),
        )
        db.add(cc)
        await db.flush()
        return cc

    @staticmethod
    async def issue_welcome_coupon(db: AsyncSession, customer_id: int) -> CustomerCoupon | None:
        """注册会员赠送新客券（每人一张）。"""
        tmpl = (
            await db.execute(
                select(CouponTemplate).where(
                    CouponTemplate.name == WELCOME_COUPON_NAME, CouponTemplate.status == 1
                )
            )
        ).scalar_one_or_none()
        if not tmpl:
            return None
        exists = await db.scalar(
            select(CustomerCoupon.id)
            .where(CustomerCoupon.customer_id == customer_id, CustomerCoupon.template_id == tmpl.id)
            .limit(1)
        )
        if exists:
            return None
        return await CouponService.issue_from_template(db, customer_id, WELCOME_COUPON_NAME)

    @staticmethod
    async def resolve_discount(
        db: AsyncSession,
        customer_coupon_id: int | None,
        customer_id: int | None,
        eligible_subtotal: Decimal,
    ) -> tuple[Decimal, int | None]:
        """校验并计算优惠券减免。eligible_subtotal=会员折后商品小计。"""
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
    ) -> tuple[Decimal, int | None]:
        """校验并计算代金券抵扣。payable_amount=会员折后小计+打包费-优惠券减免。"""
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
        now = now_local()
        expires = ensure_local_aware(cc.expires_at)
        if expires and expires <= now:
            cc.status = "EXPIRED"
            await db.flush()
            raise AppException(40074, "代金券已过期")
        payable = money(payable_amount)
        min_amt = money(tmpl.min_order_amount or 0)
        if payable < min_amt:
            raise AppException(40075, f"应付未满{coupon_service_min_label(min_amt)}，不可用此代金券")
        disc = min(money(tmpl.discount_value), payable)
        return disc, cc.id

    @staticmethod
    async def reserve_for_order(
        db: AsyncSession,
        coupon_id: int | None,
        *,
        order_id: int,
        customer_id: int | None,
    ) -> None:
        """下单后预占券，防并发双花。"""
        if not coupon_id or not customer_id:
            return
        row = (
            await db.execute(
                select(CustomerCoupon)
                .where(
                    CustomerCoupon.id == int(coupon_id),
                    CustomerCoupon.customer_id == int(customer_id),
                    CustomerCoupon.status == "AVAILABLE",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not row:
            raise AppException(40063, "优惠券不可用或已被占用")
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

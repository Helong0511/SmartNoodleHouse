"""顾客服务卡：POS/迎宾脱敏摘要（无证件原图）。"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import money, naive_local_now, serialize_dt
from app.models import (
    CouponTemplate,
    Customer,
    CustomerCoupon,
    CustomerFavoriteProduct,
    CustomerLifecycle,
    CustomerPreference,
    CustomerSocialIdentity,
    MemberLevel,
    Order,
    OrderItem,
    Product,
    SocialBenefitRedemption,
    SocialIdentityType,
)


class ServiceCardService:
    @staticmethod
    def _mask_phone(phone: str | None) -> str | None:
        if not phone:
            return None
        p = str(phone).strip()
        if len(p) < 7:
            return "***"
        return f"{p[:3]}****{p[-4:]}"

    @staticmethod
    def _lifecycle(customer: Customer, *, now=None, thresholds: dict | None = None) -> str:
        from app.services.lifecycle_service import compute_lifecycle_code

        return compute_lifecycle_code(customer, thresholds=thresholds, now=now or naive_local_now())

    @classmethod
    async def build(
        cls,
        db: AsyncSession,
        customer_id: int,
        *,
        store_id: int | None = None,
    ) -> dict:
        customer = await db.get(Customer, int(customer_id))
        if not customer:
            raise AppException(40401, "顾客不存在")

        level_name = None
        discount_rate = None
        if customer.member_level_id:
            lv = await db.get(MemberLevel, int(customer.member_level_id))
            if lv:
                level_name = lv.name
                discount_rate = str(lv.discount_rate)

        # 常点：收藏 + 近 90 天 Top 商品
        fav_q = (
            select(Product.id, Product.name)
            .join(CustomerFavoriteProduct, CustomerFavoriteProduct.product_id == Product.id)
            .where(CustomerFavoriteProduct.customer_id == customer.id)
            .order_by(CustomerFavoriteProduct.id.desc())
            .limit(5)
        )
        if store_id is not None:
            fav_q = fav_q.where(CustomerFavoriteProduct.store_id == int(store_id))
        favorites = [
            {"product_id": int(pid), "name": name}
            for pid, name in (await db.execute(fav_q)).all()
        ]

        since = naive_local_now() - timedelta(days=90)
        top_q = (
            select(
                OrderItem.product_id,
                OrderItem.product_name_snapshot,
                func.sum(OrderItem.quantity).label("qty"),
            )
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.customer_id == customer.id,
                Order.payment_status == "PAID",
                Order.created_at >= since,
            )
            .group_by(OrderItem.product_id, OrderItem.product_name_snapshot)
            .order_by(desc("qty"))
            .limit(5)
        )
        if store_id is not None:
            top_q = top_q.where(Order.store_id == int(store_id))
        frequent = [
            {"product_id": int(pid) if pid else None, "name": pname, "qty": int(qty or 0)}
            for pid, pname, qty in (await db.execute(top_q)).all()
        ]

        # 口味偏好 / 忌口：高分偏好 vs 低分
        prefs = list(
            (
                await db.execute(
                    select(CustomerPreference)
                    .where(CustomerPreference.customer_id == customer.id)
                    .order_by(CustomerPreference.preference_score.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        likes = []
        aversions = []
        for p in prefs:
            label = f"{p.category}·{(p.option_name or p.option_code)}" if p.category else str(
                p.option_name or p.option_code
            )
            item = {
                "category": p.category,
                "option_code": p.option_code,
                "label": label,
                "score": float(p.preference_score or 0),
            }
            score = float(p.preference_score or 0)
            cat = (p.category or "").lower()
            name = (p.option_name or p.option_code or "").lower()
            if "忌" in cat or "忌" in name or "不要" in name or score < 0.15:
                aversions.append(item)
            elif score >= 0.35:
                likes.append(item)
        likes = likes[:5]
        aversions = aversions[:5]

        # 可用券（收银选券用：含类型与摘要；仍不回传明文手机）
        from app.services.coupon_service import CouponService

        coupon_rows = list(
            (
                await db.execute(
                    select(CustomerCoupon, CouponTemplate)
                    .join(CouponTemplate, CouponTemplate.id == CustomerCoupon.template_id)
                    .where(
                        CustomerCoupon.customer_id == customer.id,
                        CustomerCoupon.status == "AVAILABLE",
                        CustomerCoupon.expires_at > naive_local_now(),
                    )
                    .order_by(CustomerCoupon.expires_at.asc())
                    .limit(20)
                )
            ).all()
        )
        coupons = []
        for cc, tmpl in coupon_rows:
            is_voucher = CouponService.is_voucher_type(tmpl.coupon_type)
            coupons.append(
                {
                    "id": int(cc.id),
                    "name": tmpl.name,
                    "coupon_type": tmpl.coupon_type,
                    "kind": "VOUCHER" if is_voucher else "COUPON",
                    "summary": CouponService.summary_label(tmpl),
                    "discount_label": CouponService.discount_label(tmpl),
                    "min_order_amount": str(tmpl.min_order_amount or "0.00"),
                    "expires_at": serialize_dt(cc.expires_at),
                }
            )

        # 公益身份（无证件图）
        id_rows = list(
            (
                await db.execute(
                    select(CustomerSocialIdentity, SocialIdentityType.name)
                    .outerjoin(
                        SocialIdentityType,
                        SocialIdentityType.code == CustomerSocialIdentity.identity_code,
                    )
                    .where(
                        CustomerSocialIdentity.customer_id == customer.id,
                        CustomerSocialIdentity.status.in_(["ACTIVE", "PENDING"]),
                    )
                    .order_by(CustomerSocialIdentity.id.desc())
                    .limit(10)
                )
            ).all()
        )
        identities = []
        for row, type_name in id_rows:
            identities.append(
                {
                    "identity_code": row.identity_code,
                    "identity_name": type_name or row.identity_code,
                    "status": row.status,
                    # 刻意不返回 credential_media_url
                    "credential_note": (row.credential_note or "")[:40] or None,
                }
            )

        today_start = naive_local_now().replace(hour=0, minute=0, second=0, microsecond=0)
        # 台账状态：PENDING=预占，NORMAL=已核销（无 CONFIRMED）
        redeemed_q = select(func.count()).select_from(SocialBenefitRedemption).where(
            SocialBenefitRedemption.customer_id == customer.id,
            SocialBenefitRedemption.status.in_(["PENDING", "NORMAL"]),
            SocialBenefitRedemption.created_at >= today_start,
        )
        if store_id is not None:
            redeemed_q = redeemed_q.where(SocialBenefitRedemption.store_id == int(store_id))
        today_social_used = int(await db.scalar(redeemed_q) or 0) > 0

        bal = (customer.balance_principal or Decimal("0")) + (customer.balance_gift or Decimal("0"))
        display = (customer.display_name or customer.nickname or "").strip() or None
        address = f"{display or '顾客'}您好" if customer.member_status == "MEMBER" else "您好"

        lc_row = (
            await db.execute(
                select(CustomerLifecycle).where(
                    CustomerLifecycle.customer_id == int(customer.id)
                )
            )
        ).scalar_one_or_none()
        lifecycle = (
            lc_row.lifecycle_code
            if lc_row and lc_row.lifecycle_code
            else cls._lifecycle(customer)
        )

        return {
            "customer_id": int(customer.id),
            "customer_no": customer.customer_no,
            "display_name": display,
            "greeting": address,
            "phone_masked": cls._mask_phone(customer.phone),
            "member_status": customer.member_status,
            "lifecycle": lifecycle,
            "rfm_segment": (lc_row.rfm_segment if lc_row else None),
            "level_name": level_name,
            "discount_rate": discount_rate,
            "points": int(customer.points or 0),
            "balance_total": str(money(bal)),
            "frequent_products": frequent,
            "favorites": favorites,
            "taste_likes": likes,
            "taste_aversions": aversions,
            "available_coupons": coupons,
            "coupon_count": len(coupons),
            "social_identities": identities,
            "social_today_used": today_social_used,
            "last_order_at": serialize_dt(customer.last_order_at),
        }

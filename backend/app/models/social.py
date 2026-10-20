from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

class SocialIdentityType(Base, TimestampMixin):
    """社会贡献身份类型字典。"""

    __tablename__ = "social_identity_types"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=1)
    default_verify_hints: Mapped[dict | list | None] = mapped_column(JSON, default=None)


class CustomerSocialIdentity(Base, TimestampMixin):
    """顾客公益身份认定。"""

    __tablename__ = "customer_social_identities"
    __table_args__ = (
        Index("idx_csi_customer_status", "customer_id", "status"),
        Index("idx_csi_store_status", "store_id", "status"),
        Index("idx_csi_code_status", "identity_code", "status"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    identity_code: Mapped[str] = mapped_column(String(32))
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    verify_method: Mapped[str | None] = mapped_column(String(50), default=None)
    credential_note: Mapped[str | None] = mapped_column(String(255), default=None)
    credential_media_url: Mapped[str | None] = mapped_column(String(500), default=None)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    reject_reason: Mapped[str | None] = mapped_column(String(255), default=None)


class SocialBenefitRule(Base, TimestampMixin):
    """社会贡献权益规则。"""

    __tablename__ = "social_benefit_rules"
    __table_args__ = (
        Index("idx_sbr_store_code_status", "store_id", "identity_code", "status"),
        Index("idx_sbr_priority", "priority", "status"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id", ondelete="CASCADE"), default=None)
    identity_code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))
    time_windows: Mapped[list | None] = mapped_column(JSON, default=None)
    weekdays: Mapped[list | None] = mapped_column(JSON, default=None)
    date_from: Mapped[date | None] = mapped_column(Date, default=None)
    date_to: Mapped[date | None] = mapped_column(Date, default=None)
    benefit_type: Mapped[str] = mapped_column(String(30), default="ORDER_DISCOUNT")
    discount_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    coupon_template_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("coupon_templates.id"), default=None
    )
    gift_product_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("products.id"), default=None)
    applicable_category_ids: Mapped[list | None] = mapped_column(JSON, default=None)
    applicable_product_ids: Mapped[list | None] = mapped_column(JSON, default=None)
    daily_limit: Mapped[int] = mapped_column(Integer, default=1)
    monthly_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    budget_action: Mapped[str] = mapped_column(String(10), default="WARN")
    stack_policy: Mapped[str] = mapped_column(String(30), default="EXCLUSIVE")
    max_subsidy_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[int] = mapped_column(Integer, default=1)


class SocialBenefitRedemption(Base, TimestampMixin):
    """社会贡献核销台账。"""

    __tablename__ = "social_benefit_redemptions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_sbrd_idem"),
        Index("idx_sbrd_customer_store_day", "customer_id", "store_id", "created_at"),
        Index("idx_sbrd_order", "order_id"),
        Index("idx_sbrd_rule_status", "rule_id", "status"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"), index=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("orders.id"), default=None)
    identity_code: Mapped[str] = mapped_column(String(32))
    rule_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("social_benefit_rules.id"))
    benefit_type: Mapped[str] = mapped_column(String(30))
    subsidy_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    coupon_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("customer_coupons.id"), default=None)
    operator_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), default=None)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    meta: Mapped[dict | None] = mapped_column(JSON, default=None)

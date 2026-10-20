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

class CustomerSegment(Base, TimestampMixin):
    __tablename__ = "customer_segments"
    __table_args__ = (
        UniqueConstraint("store_id", "code", name="uk_cseg_store_code"),
        Index("idx_cseg_status", "status"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id", ondelete="CASCADE"), default=None)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(100))
    segment_type: Mapped[str] = mapped_column(String(20), default="DYNAMIC")
    rules_json: Mapped[dict | list | None] = mapped_column(JSON, default=None)
    status: Mapped[int] = mapped_column(Integer, default=1)
    estimated_count: Mapped[int | None] = mapped_column(Integer, default=None)
    materialized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class CustomerSegmentMember(Base):
    __tablename__ = "customer_segment_members"
    __table_args__ = (
        UniqueConstraint("segment_id", "customer_id", name="uk_csm_seg_customer"),
        Index("idx_csm_customer", "customer_id"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    segment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customer_segments.id", ondelete="CASCADE"))
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketingCampaign(Base, TimestampMixin):
    __tablename__ = "marketing_campaigns"
    __table_args__ = (
        UniqueConstraint("store_id", "code", name="uk_mc_store_code"),
        Index("idx_mc_status_time", "status", "start_at", "end_at"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id", ondelete="CASCADE"), default=None)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(100))
    campaign_type: Mapped[str] = mapped_column(String(30), default="MEMBER_DAY")
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    segment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customer_segments.id"), default=None
    )
    coupon_template_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("coupon_templates.id"), default=None
    )
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    budget_cap: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    weekly_cap_override: Mapped[int | None] = mapped_column(Integer, default=None)
    boost_product_ids: Mapped[list | None] = mapped_column(JSON, default=None)
    remark: Mapped[str | None] = mapped_column(String(500), default=None)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), default=None)


class MarketingRule(Base, TimestampMixin):
    __tablename__ = "marketing_rules"
    __table_args__ = (Index("idx_mr_campaign", "campaign_id", "status"),)
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    campaign_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("marketing_campaigns.id", ondelete="CASCADE"))
    rule_type: Mapped[str] = mapped_column(String(30), default="SEGMENT")
    config_json: Mapped[dict | list | None] = mapped_column(JSON, default=None)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[int] = mapped_column(Integer, default=1)


class MarketingTask(Base, TimestampMixin):
    __tablename__ = "marketing_tasks"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_mt_idem"),
        Index("idx_mt_status_sched", "status", "scheduled_at"),
        Index("idx_mt_campaign", "campaign_id", "status"),
        Index("idx_mt_customer", "customer_id"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    campaign_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("marketing_campaigns.id", ondelete="CASCADE"))
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id", ondelete="CASCADE"))
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None)
    action_type: Mapped[str] = mapped_column(String(30), default="ISSUE_COUPON")
    payload_json: Mapped[dict | None] = mapped_column(JSON, default=None)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    try_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500), default=None)
    result_json: Mapped[dict | None] = mapped_column(JSON, default=None)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class MarketingMessageLog(Base, TimestampMixin):
    __tablename__ = "marketing_message_logs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_mml_idem"),
        Index("idx_mml_customer_sent", "customer_id", "sent_at"),
        Index("idx_mml_campaign_status", "campaign_id", "status"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None)
    campaign_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("marketing_campaigns.id"), default=None
    )
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(20), default="WECHAT")
    template_key: Mapped[str] = mapped_column(String(64))
    template_id: Mapped[str | None] = mapped_column(String(120), default=None)
    payload_json: Mapped[dict | None] = mapped_column(JSON, default=None)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    error_message: Mapped[str | None] = mapped_column(String(500), default=None)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class CampaignAttribution(Base, TimestampMixin):
    __tablename__ = "campaign_attributions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_ca_idem"),
        Index("idx_ca_campaign_status", "campaign_id", "status"),
        Index("idx_ca_order", "order_id"),
        Index("idx_ca_customer", "customer_id"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    campaign_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("marketing_campaigns.id", ondelete="CASCADE"))
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id", ondelete="CASCADE"))
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id", ondelete="CASCADE"))
    attribution_type: Mapped[str] = mapped_column(String(20), default="COUPON")
    coupon_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("customer_coupons.id"), default=None)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    subsidy_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    meta: Mapped[dict | None] = mapped_column(JSON, default=None)
    attributed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

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
        # 应用维护的唯一键：store_id 为空（总部）时 MySQL 的 NULL 不去重，靠该列兜底
        UniqueConstraint("code_key", name="uk_mc_code_key"),
        Index("idx_mc_status_time", "status", "start_at", "end_at"),
        Index("idx_mc_schedule_mode", "schedule_mode", "status"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id", ondelete="CASCADE"), default=None)
    code: Mapped[str] = mapped_column(String(64))
    # HQ:{CODE} 或 {store_id}:{CODE}；由服务层统一写入并用于跨库唯一校验
    code_key: Mapped[str | None] = mapped_column(String(96), default=None)
    name: Mapped[str] = mapped_column(String(100))
    campaign_type: Mapped[str] = mapped_column(String(30), default="MEMBER_DAY")
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    segment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customer_segments.id"), default=None
    )
    coupon_template_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("coupon_templates.id"), default=None
    )
    # ABSOLUTE=绝对起止；ANNUAL_MD=每年月日自动启停（公历/农历）
    schedule_mode: Mapped[str] = mapped_column(String(20), default="ABSOLUTE")
    schedule_calendar: Mapped[str] = mapped_column(String(10), default="SOLAR")
    annual_start_month: Mapped[int | None] = mapped_column(Integer, default=None)
    annual_start_day: Mapped[int | None] = mapped_column(Integer, default=None)
    annual_end_month: Mapped[int | None] = mapped_column(Integer, default=None)
    annual_end_day: Mapped[int | None] = mapped_column(Integer, default=None)
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


class MarketingCampaignStore(Base, TimestampMixin):
    """活动参与门店范围。

    总部活动（campaign.store_id 为空）默认全部门店参与；本表仅记录例外。
    included=False 表示该门店被排除；门店活动自身不受本表影响。
    """

    __tablename__ = "marketing_campaign_stores"
    __table_args__ = (
        UniqueConstraint("campaign_id", "store_id", name="uk_mcs_camp_store"),
        Index("idx_mcs_store", "store_id", "campaign_id"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    campaign_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("marketing_campaigns.id", ondelete="CASCADE")
    )
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id", ondelete="CASCADE"))
    included: Mapped[bool] = mapped_column(Boolean, default=False)


class FlashSaleConfig(Base, TimestampMixin):
    """限量免费抢购配置：与活动 1:1，库存按门店拆分（FlashSaleStock）。"""

    __tablename__ = "marketing_flash_sales"
    __table_args__ = (UniqueConstraint("campaign_id", name="uk_mfs_campaign"),)
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    campaign_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("marketing_campaigns.id", ondelete="CASCADE")
    )
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    per_customer_limit: Mapped[int] = mapped_column(Integer, default=1)
    # 抢到资格后须在该分钟数内完成核销
    claim_valid_minutes: Mapped[int] = mapped_column(Integer, default=30)
    # 绝对核销截止（可空；与活动结束时间取更早者）
    verify_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    notice: Mapped[str | None] = mapped_column(String(200), default=None)


class FlashSaleStock(Base, TimestampMixin):
    """活动门店库存：扣减必须走条件 UPDATE，不允许先查后减。"""

    __tablename__ = "marketing_flash_sale_stocks"
    __table_args__ = (
        UniqueConstraint("campaign_id", "store_id", name="uk_mfss_camp_store"),
        Index("idx_mfss_store", "store_id", "campaign_id"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    campaign_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("marketing_campaigns.id", ondelete="CASCADE")
    )
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id", ondelete="CASCADE"))
    # 门店专属抢购餐品；为空时回退 FlashSaleConfig.product_id（仅单店活动可回退）
    product_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("products.id"), default=None
    )
    total_quantity: Mapped[int] = mapped_column(Integer, default=0)
    remaining_quantity: Mapped[int] = mapped_column(Integer, default=0)


class FlashSaleClaim(Base, TimestampMixin):
    """顾客抢购资格；unit_no 配合唯一键实现每人限购。"""

    __tablename__ = "marketing_flash_sale_claims"
    __table_args__ = (
        UniqueConstraint("campaign_id", "customer_id", "unit_no", name="uk_mfsc_cust_unit"),
        UniqueConstraint("claim_no", name="uk_mfsc_no"),
        UniqueConstraint("client_request_id", name="uk_mfsc_req"),
        Index("idx_mfsc_customer_status", "customer_id", "status"),
        Index("idx_mfsc_status_expire", "status", "expires_at"),
        Index("idx_mfsc_campaign_store", "campaign_id", "store_id", "status"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    campaign_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("marketing_campaigns.id", ondelete="CASCADE")
    )
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id", ondelete="CASCADE"))
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id", ondelete="CASCADE"))
    unit_no: Mapped[int] = mapped_column(Integer, default=1)
    claim_no: Mapped[str] = mapped_column(String(40))
    # CLAIMED=待核销；USED=已核销；EXPIRED=超时失效；CANCELLED=活动终止
    status: Mapped[str] = mapped_column(String(20), default="CLAIMED")
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verify_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    order_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    verified_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), default=None)
    client_request_id: Mapped[str | None] = mapped_column(String(64), default=None)

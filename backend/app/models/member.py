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

class MemberLevel(Base):
    __tablename__ = "member_levels"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50))
    min_points: Mapped[int] = mapped_column(Integer, default=0)
    discount_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("1.00"))


class CouponTemplate(Base):
    __tablename__ = "coupon_templates"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # NULL=总部/全店可见；非空=仅该门店
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None, index=True)
    name: Mapped[str] = mapped_column(String(100))
    coupon_type: Mapped[str] = mapped_column(String(20), default="FIXED")
    discount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    min_order_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    valid_days: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[int] = mapped_column(Integer, default=1)
    # P0-6: 库存控制（NULL=不限量）
    total_quota: Mapped[int | None] = mapped_column(Integer, default=None, comment="发放总量上限；NULL=不限")
    remaining: Mapped[int | None] = mapped_column(Integer, default=None, comment="剩余可发量；NULL=不限")
    # P2-4: 总部模板（store_id=NULL）的门店白名单；NULL=全部门店可见，非空数组=仅列出门店可见
    store_whitelist: Mapped[list | None] = mapped_column(
        JSON, default=None, nullable=True, comment="总部模板可见门店白名单；NULL=全部门店"
    )
    # ALWAYS=全年可发；ANNUAL_MD=仅公历/农历月日窗口内可发（节日券）
    schedule_mode: Mapped[str] = mapped_column(String(20), default="ALWAYS")
    schedule_calendar: Mapped[str] = mapped_column(String(10), default="SOLAR")
    annual_start_month: Mapped[int | None] = mapped_column(Integer, default=None)
    annual_start_day: Mapped[int | None] = mapped_column(Integer, default=None)
    annual_end_month: Mapped[int | None] = mapped_column(Integer, default=None)
    annual_end_day: Mapped[int | None] = mapped_column(Integer, default=None)


class CustomerCoupon(Base):
    __tablename__ = "customer_coupons"
    __table_args__ = (
        Index("ix_cc_customer_expires_status", "customer_id", "expires_at", "status"),
        Index("ix_cc_customer_template", "customer_id", "template_id"),
    )
    # 同模板可持有多张（满减/折扣/代金券均由业务按张核销）
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    template_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("coupon_templates.id"), index=True)
    coupon_no: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="AVAILABLE")
    status_reason: Mapped[str | None] = mapped_column(String(40), default=None)
    obtained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_order_id: Mapped[int | None] = mapped_column(BigInteger)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # P0-6: 客户端幂等键（NULL 多行不冲突；非空时 UNIQUE 保证重复请求只成功一次）
    client_request_id: Mapped[str | None] = mapped_column(String(128), default=None, unique=True, comment="客户端幂等键")
    # 发券人信息：记录谁发的券、在哪个门店发的（券只能在该门店使用）
    issued_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), default=None, comment="发券人 user_id", index=True)
    issued_at_store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None, comment="发券门店；NULL=不限门店使用", index=True)


class OrderMilestoneReward(Base, TimestampMixin):
    """门店累计成功支付单数里程碑 → 自动发券规则。"""

    __tablename__ = "order_milestone_rewards"
    __table_args__ = (
        UniqueConstraint("store_id", "threshold_orders", name="uk_omr_store_threshold"),
        Index("idx_omr_store_status", "store_id", "status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    threshold_orders: Mapped[int] = mapped_column(Integer, comment="累计成功支付订单数阈值")
    template_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("coupon_templates.id"), index=True)
    status: Mapped[int] = mapped_column(Integer, default=1)


class PointLedger(Base):
    """会员积分流水（与 customers.points 对账）"""
    __tablename__ = "point_ledger"
    __table_args__ = (Index("ix_pl_created_at_customer_id", "created_at", "customer_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("orders.id"), index=True)
    refund_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payment_refunds.id"), unique=True, comment="退款幂等键：每单至多一条扣减", index=True
    )
    delta: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    entry_type: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BalanceLedger(Base):
    """会员储值流水（本金/赠送金，与 customers.balance_* 对账）"""

    __tablename__ = "balance_ledger"
    __table_args__ = (Index("ix_bl_created_at_customer_id", "created_at", "customer_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"), index=True)
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None)
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("orders.id"), default=None)
    recharge_order_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    payment_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("payments.id"), default=None)
    refund_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payment_refunds.id"), unique=True, default=None
    )
    operator_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), default=None, index=True)
    biz_no: Mapped[str | None] = mapped_column(String(40), unique=True, default=None)
    entry_type: Mapped[str] = mapped_column(String(30))
    delta_principal: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    delta_gift: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    principal_after: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    gift_after: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    remark: Mapped[str | None] = mapped_column(String(255), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RechargePackage(Base, TimestampMixin):
    __tablename__ = "recharge_packages"
    __table_args__ = (
        # MySQL 用生成列 scope_store_id=IFNULL(store_id,0) 实现总部唯一；此处与业务校验对齐
        Index("idx_rp_store_status", "store_id", "status", "sort_order"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None, index=True)
    name: Mapped[str] = mapped_column(String(100))
    pay_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    gift_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=1)


class RechargeOrder(Base, TimestampMixin):
    __tablename__ = "recharge_orders"
    __table_args__ = (
        UniqueConstraint("recharge_no", name="uk_ro_no"),
        UniqueConstraint("idempotency_key", name="uk_ro_idem"),
        Index("ix_ro_customer_created", "customer_id", "created_at"),
        Index("ix_ro_store_status_created", "store_id", "status", "created_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recharge_no: Mapped[str] = mapped_column(String(40))
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    package_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("recharge_packages.id"), default=None)
    pay_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    credit_principal: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    credit_gift: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    channel: Mapped[str | None] = mapped_column(String(20), default=None)
    payment_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), default=None)


class FinancialLedger(Base):
    """统一财务分录（收款/退款/调账）"""
    __tablename__ = "financial_ledger"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    entry_type: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str | None] = mapped_column(String(30))
    provider: Mapped[str | None] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("orders.id"), index=True)
    payment_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("payments.id"), index=True)
    refund_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("payment_refunds.id"), index=True)
    delivery_order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("delivery_orders.id"))
    delivery_refund_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("delivery_refunds.id"))
    remark: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 幂等唯一约束：并发回调下防重复入账（MySQL 唯一索引对 NULL 不生效，业务键缺失的行不受限）
    __table_args__ = (
        UniqueConstraint("entry_type", "payment_id", name="uq_fin_ledger_entry_payment"),
        UniqueConstraint("entry_type", "refund_id", name="uq_fin_ledger_entry_refund"),
        UniqueConstraint("entry_type", "delivery_order_id", name="uq_fin_ledger_entry_delivery_order"),
        UniqueConstraint("entry_type", "delivery_refund_id", name="uq_fin_ledger_entry_delivery_refund"),
        Index("ix_fl_channel_created_entry_store", "channel", "created_at", "entry_type", "store_id"),
        Index("ix_fl_created_at_store_id", "created_at", "store_id"),
        Index("ix_fl_created_at_entry_type", "created_at", "entry_type"),
    )


class PointExchangeOption(Base, TimestampMixin):
    """积分兑换选项：用积分换券模板。store_id=NULL 表示总部全局选项。"""

    __tablename__ = "point_exchange_options"
    __table_args__ = (
        UniqueConstraint("store_id", "name", name="uk_peo_store_name"),
        Index("idx_peo_store_status", "store_id", "status", "sort_order"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    store_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("stores.id", ondelete="CASCADE"), default=None, index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    template_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("coupon_templates.id"), index=True
    )
    points_cost: Mapped[int] = mapped_column(Integer)
    status: Mapped[int] = mapped_column(Integer, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

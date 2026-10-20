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

class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_kitchen_claimed_status", "kitchen_claimed_by", "status"),
        Index("ix_orders_created_at_store_id", "created_at", "store_id"),
        Index("ix_orders_paid_at_payment_status_store", "paid_at", "payment_status", "store_id"),
        Index("ix_orders_created_at_customer_id", "created_at", "customer_id"),
        Index("ix_orders_paid_at_status", "paid_at", "status"),
        Index("ix_orders_created_at_payment_status", "created_at", "payment_status"),
        Index("ix_orders_created_at_table_id", "created_at", "table_id"),
        Index("ix_orders_created_at_status", "created_at", "status"),
        Index("ix_orders_paid_at_store_id", "paid_at", "store_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("customers.id"))
    table_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("tables.id"))
    dining_session_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("dining_sessions.id"), default=None, index=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    status: Mapped[str] = mapped_column(String(20), default="PENDING_PAYMENT")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    customer_coupon_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("customer_coupons.id"))
    coupon_discount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    customer_voucher_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("customer_coupons.id"))
    voucher_discount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    packing_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    payment_status: Mapped[str] = mapped_column(String(20), default="UNPAID")
    payment_method: Mapped[str | None] = mapped_column(String(20))
    channel: Mapped[str | None] = mapped_column(String(20))
    dining_type: Mapped[str] = mapped_column(String(20), default="DINE_IN")
    remark: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    operator_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), default=None, index=True)
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kitchen_claimed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), default=None)
    kitchen_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 顾客自助催单：累计次数 + 最近催单时间（KDS 高亮用）
    urge_count: Mapped[int] = mapped_column(Integer, default=0)
    urged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # 预点单：打包单预约取餐时段（仅 TAKEOUT 类型使用）
    scheduled_pickup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"), index=True)
    product_name_snapshot: Mapped[str] = mapped_column(String(100))
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    remark: Mapped[str | None] = mapped_column(String(255))


class OrderItemOption(Base):
    __tablename__ = "order_item_options"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_item_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("order_items.id", ondelete="CASCADE"), index=True)
    group_name_snapshot: Mapped[str] = mapped_column(String(50))
    option_name_snapshot: Mapped[str] = mapped_column(String(50))
    option_code: Mapped[str] = mapped_column(String(50))
    extra_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    __table_args__ = (Index("ix_osh_created_at_order_id", "created_at", "order_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id", ondelete="CASCADE"))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(30))
    operator_id: Mapped[int | None] = mapped_column(BigInteger)
    remark: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PrintJob(Base, TimestampMixin):
    """厨房小票打印任务：落库 + 失败指数退避重试。"""

    __tablename__ = "print_jobs"
    __table_args__ = (
        Index("ix_pj_order_ticket_type", "order_id", "ticket_type"),
        Index("ix_pj_locked_at_locked_by", "locked_at", "locked_by"),
        Index("ix_pj_next_retry_at_status", "next_retry_at", "status"),
        Index("ix_pj_created_at_status_store", "created_at", "status", "store_id"),
    )

    # SQLite 仅 INTEGER PRIMARY KEY 可自增；MySQL 仍用 BIGINT
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id", ondelete="CASCADE"))
    ticket_type: Mapped[str] = mapped_column(String(20), default="KITCHEN")
    content_hash: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArchivedOrderSnapshot(Base):
    __tablename__ = "archived_order_snapshots"
    __table_args__ = (
        UniqueConstraint("order_id", name="uk_aos_order_id"),
        UniqueConstraint("order_no", name="uk_aos_order_no"),
        Index("idx_aos_store_created", "store_id", "original_created_at"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    order_id: Mapped[int] = mapped_column(BigInteger)
    store_id: Mapped[int] = mapped_column(BigInteger)
    order_no: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    payment_status: Mapped[str | None] = mapped_column(String(32), default=None)
    original_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

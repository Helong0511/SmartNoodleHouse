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

class Payment(Base, TimestampMixin):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("provider", "provider_trade_no"),
        Index("ix_payments_operator_paid_at", "operator_id", "paid_at"),
        Index("ix_payments_paid_at_status", "paid_at", "status"),
        Index("ix_payments_order_id_status", "order_id", "status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payment_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("orders.id"), index=True, default=None)
    recharge_order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("recharge_orders.id"), index=True, default=None
    )
    customer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("customers.id"), index=True)
    operator_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str] = mapped_column(String(30))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    provider_trade_no: Mapped[str | None] = mapped_column(String(64))
    provider_order_no: Mapped[str | None] = mapped_column(String(64))
    callback_raw: Mapped[dict | None] = mapped_column(JSON)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class PaymentRefund(Base, TimestampMixin):
    __tablename__ = "payment_refunds"
    __table_args__ = (
        Index("ix_pr_status_succeeded_at", "status", "succeeded_at"),
        Index("ix_pr_next_retry_at_status", "next_retry_at", "status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    refund_no: Mapped[str] = mapped_column(String(40), unique=True)
    payment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payments.id"), index=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    reason: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    provider_refund_no: Mapped[str | None] = mapped_column(String(64))
    callback_raw: Mapped[dict | None] = mapped_column(JSON)
    fail_reason: Mapped[str | None] = mapped_column(String(255))
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operator_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)


class CashierShift(Base, TimestampMixin):
    __tablename__ = "cashier_shifts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    operator_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    opening_float: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_cash: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    total_wechat: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    total_alipay: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    closing_cash: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    cash_difference: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    closed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    remark: Mapped[str | None] = mapped_column(String(500))


class PosRefundRequest(Base, TimestampMixin):
    """收银退款申请：收银员提交，店长/超管审批并执行。"""

    __tablename__ = "pos_refund_requests"
    __table_args__ = (
        Index("ix_prr_created_at_requested_by", "created_at", "requested_by"),
        Index("ix_prr_created_at_status_store", "created_at", "status", "store_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"), index=True)
    payment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payments.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    executed_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    reason: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    # 顾客自助申请时为 NULL（小程序未开始制作订单的全额退款申请）
    requested_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_remark: Mapped[str | None] = mapped_column(String(500))
    refund_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("payment_refunds.id"))
    fail_reason: Mapped[str | None] = mapped_column(String(255))


class PaymentStatusHistory(Base):
    __tablename__ = "payment_status_history"
    __table_args__ = (Index("ix_psh_created_at_payment_id", "created_at", "payment_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payments.id", ondelete="CASCADE"))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(30))
    remark: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentCallbackReceipt(Base):
    __tablename__ = "payment_callback_receipts"
    __table_args__ = (UniqueConstraint("provider", "receipt_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(20))
    receipt_key: Mapped[str] = mapped_column(String(128))
    payment_no: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentReconcileBatch(Base, TimestampMixin):
    __tablename__ = "payment_reconcile_batches"
    __table_args__ = (
        UniqueConstraint(
            "channel", "bill_date", "scope_store_id", name="uk_prb_channel_bill_store"
        ),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    channel: Mapped[str] = mapped_column(String(20))
    bill_date: Mapped[date] = mapped_column(Date)
    # 0 表示总部/平台级对账；正数表示门店独立账单范围。
    # 不加 stores 外键是为了让 0 作为总部作用域哨兵值，并保留迁移兼容性。
    scope_store_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0", index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    local_count: Mapped[int] = mapped_column(Integer, default=0)
    channel_count: Mapped[int] = mapped_column(Integer, default=0)
    mismatch_count: Mapped[int] = mapped_column(Integer, default=0)
    source_filename: Mapped[str | None] = mapped_column(String(255), default=None)
    error_message: Mapped[str | None] = mapped_column(String(500), default=None)
    created_by: Mapped[int | None] = mapped_column(BigInteger, default=None)


class PaymentReconcileDiff(Base, TimestampMixin):
    __tablename__ = "payment_reconcile_diffs"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    batch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payment_reconcile_batches.id", ondelete="CASCADE"), index=True
    )
    diff_type: Mapped[str] = mapped_column(String(32))
    local_payment_no: Mapped[str | None] = mapped_column(String(64), default=None)
    channel_trade_no: Mapped[str | None] = mapped_column(String(128), default=None)
    local_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    channel_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    detail: Mapped[dict | None] = mapped_column(JSON, default=None)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")

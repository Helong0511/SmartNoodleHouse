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
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin

class WebhookInbox(Base):
    __tablename__ = "webhook_inbox"
    __table_args__ = (
        UniqueConstraint("channel", "event_id"),
        Index("ix_wi_received_at_status", "received_at", "status"),
        Index("ix_wi_locked_at_locked_by", "locked_at", "locked_by"),
        Index("ix_wi_next_retry_at_status", "next_retry_at", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(30))
    event_id: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str | None] = mapped_column(String(50))
    payload: Mapped[str] = mapped_column(Text)
    headers_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    error_message: Mapped[str | None] = mapped_column(String(500))
    # 原子 claim / 重试：attempt_count、退避、锁持有者
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")
    )
    locked_by: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessedMessage(Base):
    """通用消息幂等：MySQL UNIQUE 为事实源，Redis 仅作已处理缓存。"""

    __tablename__ = "processed_messages"
    __table_args__ = (
        UniqueConstraint("scope", "message_id", name="uk_processed_msg"),
        Index("ix_processed_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    scope: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[str] = mapped_column(String(128))
    meta_json: Mapped[str | None] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxEvent(Base):
    """DB 事务型 outbox：与业务状态同事务写入，由 worker 原子 claim 异步消费。

    用于支付成功副作用（积分/券核销/口味）与退款补偿，避免 Redis list 丢消息。
    幂等键：(aggregate_type, aggregate_id, event_kind)。
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("aggregate_type", "aggregate_id", "event_kind", name="uq_outbox_agg"),
        Index("ix_oe_next_retry_at_status", "next_retry_at", "status"),
        Index("ix_oe_locked_at_status", "locked_at", "status"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    aggregate_type: Mapped[str] = mapped_column(String(32))
    aggregate_id: Mapped[int] = mapped_column(BigInteger)
    event_kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str | None] = mapped_column(Text)
    # 每次库存镜像信号递增；worker 处理期间若版本变化，完成时必须重新排队。
    sync_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")
    )
    locked_by: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

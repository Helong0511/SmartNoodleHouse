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

class Camera(Base, TimestampMixin):
    __tablename__ = "cameras"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    camera_type: Mapped[str] = mapped_column(String(20))
    source_url: Mapped[str] = mapped_column(String(500))
    username: Mapped[str | None] = mapped_column(String(100))
    password_encrypted: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="OFFLINE")
    fps: Mapped[int] = mapped_column(Integer, default=25)
    detect_fps: Mapped[int] = mapped_column(Integer, default=3)
    last_online_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VisitorEvent(Base):
    __tablename__ = "visitor_events"
    __table_args__ = (
        Index("ix_ve_camera_event_time", "camera_id", "event_time"),
        Index("idx_ve_customer_time", "customer_id", "event_time"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    visitor_no: Mapped[str] = mapped_column(String(64))
    camera_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cameras.id"))
    customer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(30))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot_path: Mapped[str | None] = mapped_column(String(500))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FaceProfile(Base, TimestampMixin):
    __tablename__ = "face_profiles"
    __table_args__ = (Index("ix_fp_consent_customer", "consent_status", "customer_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    feature_data: Mapped[bytes] = mapped_column(LargeBinary)
    feature_version: Mapped[str] = mapped_column(String(20), default="v1")
    consent_status: Mapped[str] = mapped_column(String(20), default="AUTHORIZED")
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WelcomeLog(Base):
    __tablename__ = "welcome_logs"
    __table_args__ = (Index("ix_wl_created_at_customer", "created_at", "customer_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    camera_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cameras.id"), index=True)
    welcome_text: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

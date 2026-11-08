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

class Store(Base, TimestampMixin):
    __tablename__ = "stores"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_no: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    logo: Mapped[str | None] = mapped_column(String(500), default=None)
    address: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(20))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), default=None)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), default=None)
    status: Mapped[int] = mapped_column(Integer, default=1)
    # Durable menu cache generation; Redis is a rebuildable acceleration layer.
    menu_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1_000_000_000, server_default="1000000000")
    # -1 represents no active category schedule.
    menu_schedule_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=-1, server_default="-1")


class DiningTable(Base, TimestampMixin):
    __tablename__ = "tables"
    __table_args__ = (UniqueConstraint("store_id", "table_no", name="uk_tables_store_no"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    table_no: Mapped[str] = mapped_column(String(20))
    table_name: Mapped[str] = mapped_column(String(50))
    qr_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    wechat_qr_url: Mapped[str | None] = mapped_column(String(500))
    alipay_qr_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[int] = mapped_column(Integer, default=1)


class StoreSetting(Base):
    """门店业务参数覆盖（覆盖全局 system_settings 同名键）"""
    __tablename__ = "store_settings"
    __table_args__ = (UniqueConstraint("store_id", "setting_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id", ondelete="CASCADE"))
    setting_key: Mapped[str] = mapped_column(String(100))
    setting_value: Mapped[str] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StoreBusinessHours(Base, TimestampMixin):
    """门店营业时间：每个 (门店, 星期几, 时段序号) 一行。

    close_time <= open_time 视为跨午夜（cross_day=True）。
    某天没有任何 is_open=1 的行，即当日整体休息。
    """

    __tablename__ = "store_business_hours"
    __table_args__ = (UniqueConstraint("store_id", "weekday", "slot_index", name="uk_sbh_store_day_slot"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer, comment="1=周一 ... 7=周日")
    slot_index: Mapped[int] = mapped_column(Integer, default=0, comment="当日第几时段，从0开始")
    open_time: Mapped[datetime] = mapped_column(Time)
    close_time: Mapped[datetime] = mapped_column(Time)
    cross_day: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否跨午夜")
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    setting_key: Mapped[str] = mapped_column(String(100), unique=True)
    setting_value: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

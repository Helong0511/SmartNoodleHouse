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

class DeliveryProductMap(Base, TimestampMixin):
    """平台菜品 ↔ 本系统餐品"""

    __tablename__ = "delivery_product_maps"
    __table_args__ = (UniqueConstraint("store_id", "platform", "platform_sku_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform: Mapped[str] = mapped_column(String(20))
    platform_sku_id: Mapped[str] = mapped_column(String(64))
    platform_spu_code: Mapped[str | None] = mapped_column(String(64))
    app_food_code: Mapped[str | None] = mapped_column(String(64))
    option_signature: Mapped[str | None] = mapped_column(String(128))
    platform_sku_name: Mapped[str | None] = mapped_column(String(100))
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"), index=True)
    option_map_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[int] = mapped_column(Integer, default=1)


class DeliveryOrder(Base, TimestampMixin):
    """外卖订单扩展信息"""

    __tablename__ = "delivery_orders"
    __table_args__ = (
        UniqueConstraint("platform", "platform_order_id"),
        Index("ix_do_created_at_delivery_status", "created_at", "delivery_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    platform: Mapped[str] = mapped_column(String(20))
    platform_order_id: Mapped[str] = mapped_column(String(64))
    platform_day_seq: Mapped[str | None] = mapped_column(String(32))
    delivery_status: Mapped[str] = mapped_column(String(30), default="PENDING")
    recipient_name: Mapped[str | None] = mapped_column(String(50))
    recipient_phone: Mapped[str | None] = mapped_column(String(20))
    recipient_address: Mapped[str | None] = mapped_column(String(500))
    rider_name: Mapped[str | None] = mapped_column(String(50))
    rider_phone: Mapped[str | None] = mapped_column(String(20))
    expected_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    platform_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    packing_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    logistics_status: Mapped[str | None] = mapped_column(String(30))
    logistics_code: Mapped[str | None] = mapped_column(String(50))
    refund_status: Mapped[str] = mapped_column(String(30), default="NONE")
    urge_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlatformStoreBinding(Base, TimestampMixin):
    __tablename__ = "platform_store_bindings"
    __table_args__ = (UniqueConstraint("store_id", "platform", "platform_shop_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    platform: Mapped[str] = mapped_column(String(20))
    platform_shop_id: Mapped[str] = mapped_column(String(64))
    platform_shop_name: Mapped[str | None] = mapped_column(String(100))
    access_token: Mapped[str | None] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extra_config: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[int] = mapped_column(Integer, default=1)
    # 自动开/关店开关：默认关闭，需逐门店显式开启，避免上线即影响真实营业
    auto_sync_open: Mapped[bool] = mapped_column(Boolean, default=False)
    # 最后一次同步到平台的营业状态：OPEN(营业)/REST(休息)/OFFLINE(下线)
    platform_open_status: Mapped[str | None] = mapped_column(String(20), default=None)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class PlatformCategoryMap(Base, TimestampMixin):
    """本地餐品分类 ↔ 外卖平台分类映射。store_id=NULL 表示总部默认映射。"""

    __tablename__ = "platform_category_maps"
    __table_args__ = (
        UniqueConstraint("store_id", "platform", "local_category_id", name="uk_pcm_store_platform_cat"),
        Index("ix_pcm_platform_status", "platform", "status"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    store_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("stores.id", ondelete="CASCADE"), default=None, comment="NULL=总部默认"
    )
    platform: Mapped[str] = mapped_column(String(20))
    local_category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product_categories.id", ondelete="CASCADE"), index=True)
    platform_category_code: Mapped[str | None] = mapped_column(String(64), default=None)
    platform_category_name: Mapped[str | None] = mapped_column(String(100), default=None)
    status: Mapped[int] = mapped_column(Integer, default=1)


class DeliverySyncAlert(Base):
    """外卖同步告警：拒单（库存不足）、平台同步失败等，供监控页展示。"""

    __tablename__ = "delivery_sync_alerts"
    __table_args__ = (Index("ix_dsa_created_at_resolved_store", "created_at", "resolved", "store_id"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    store_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    platform: Mapped[str | None] = mapped_column(String(20), default=None)
    alert_type: Mapped[str] = mapped_column(String(30), default="SYNC_FAIL")
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str | None] = mapped_column(Text, default=None)
    platform_order_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    resolved: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class DeliveryRefund(Base):
    __tablename__ = "delivery_refunds"
    # 平台退款编号与无编号事件指纹分别唯一；NULL 退款编号由 event_fingerprint 兜底去重。
    __table_args__ = (
        UniqueConstraint("platform", "platform_refund_id"),
        UniqueConstraint("platform", "event_fingerprint"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    delivery_order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("delivery_orders.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20))
    platform_refund_id: Mapped[str | None] = mapped_column(String(64))
    event_fingerprint: Mapped[str | None] = mapped_column(String(64))
    refund_type: Mapped[str] = mapped_column(String(30), default="FULL")
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    reason: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeliveryCallbackLog(Base):
    __tablename__ = "delivery_callback_logs"
    __table_args__ = (
        UniqueConstraint("platform", "event_id"),
        Index("ix_dcl_created_at_event_type", "created_at", "event_type"),
        Index("ix_dcl_created_at_platform_order", "created_at", "platform", "platform_order_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(20))
    event_type: Mapped[str] = mapped_column(String(50))
    event_id: Mapped[str | None] = mapped_column(String(128))
    platform_order_id: Mapped[str | None] = mapped_column(String(64))
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

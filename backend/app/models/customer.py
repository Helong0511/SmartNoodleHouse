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

class Customer(Base, TimestampMixin):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    customer_no: Mapped[str] = mapped_column(String(40), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(100))
    nickname: Mapped[str | None] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    phone: Mapped[str | None] = mapped_column(String(20), index=True)
    member_level_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("member_levels.id"), index=True)
    points: Mapped[int] = mapped_column(Integer, default=0)
    balance_principal: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    balance_gift: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    status: Mapped[int] = mapped_column(Integer, default=1)
    member_status: Mapped[str] = mapped_column(String(20), default="GUEST", index=True)
    account_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    profile_status: Mapped[str] = mapped_column(String(20), default="EMPTY")
    phone_verified: Mapped[int] = mapped_column(Integer, default=0)
    member_activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    first_seen_store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None)
    first_seen_table_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("tables.id"), default=None)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    registered_store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None, index=True)
    registered_table_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("tables.id"), default=None, index=True)
    last_order_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # 生日（仅月/日用于会员日权益；birthday_calendar 区分公历/农历）
    birthday: Mapped[date | None] = mapped_column(Date, default=None)
    birthday_calendar: Mapped[str] = mapped_column(String(10), default="SOLAR")
    # 1=小程序端已确认不可再改；后台员工仍可改
    birthday_locked: Mapped[int] = mapped_column(Integer, default=0)
    # 隐私保护默认关闭；仅顾客明确同意后开启个性化推荐
    recommend_opt_in: Mapped[int] = mapped_column(Integer, default=0)
    # 个性化推荐同意的隐私政策版本与同意时间（版本化留痕，PIPL 合规）
    consent_version: Mapped[str | None] = mapped_column(String(40), default=None)
    recommend_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class CustomerIdentity(Base, TimestampMixin):
    """服务端验证过的稳定身份；昵称、头像和手输手机号不得写入此表。"""

    __tablename__ = "customer_identities"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_subject",
            name="uk_customer_identity_provider_subject",
        ),
        Index("ix_ci_customer_id_provider", "customer_id", "provider"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    provider: Mapped[str] = mapped_column(String(32))
    provider_subject: Mapped[str] = mapped_column(String(128))
    verified: Mapped[int] = mapped_column(Integer, default=1)


class DiningSession(Base, TimestampMixin):
    __tablename__ = "dining_sessions"
    __table_args__ = (
        Index("ix_ds_customer_expires_status", "customer_id", "expires_at", "status"),
        Index("ix_ds_status_store_table", "status", "store_id", "table_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    table_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tables.id"), index=True)
    table_token: Mapped[str | None] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class TableParty(Base, TimestampMixin):
    """同桌就餐局：共享购物车容器（一人结全桌草稿）。"""

    __tablename__ = "table_parties"
    __table_args__ = (
        Index("ix_tp_store_table_status", "store_id", "table_id", "status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    table_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tables.id"), index=True)
    host_customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class TablePartyMember(Base, TimestampMixin):
    __tablename__ = "table_party_members"
    __table_args__ = (UniqueConstraint("party_id", "customer_id", name="uk_tpm_party_customer"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    party_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("table_parties.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="MEMBER")
    nickname_snap: Mapped[str | None] = mapped_column(String(100), default=None)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class SharedCartItem(Base, TimestampMixin):
    __tablename__ = "shared_cart_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    party_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("table_parties.id", ondelete="CASCADE"), index=True)
    added_by_customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"), index=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"), index=True)
    product_name: Mapped[str] = mapped_column(String(100))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    option_ids: Mapped[list | None] = mapped_column(JSON, default=None)
    option_text: Mapped[str | None] = mapped_column(String(255), default=None)
    remark: Mapped[str | None] = mapped_column(String(200), default=None)
    category_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    main_image: Mapped[str | None] = mapped_column(String(500), default=None)
    no_member_discount: Mapped[int] = mapped_column(Integer, default=0)


class CustomerWechatAccount(Base, TimestampMixin):
    __tablename__ = "customer_wechat_accounts"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"), index=True)
    openid: Mapped[str] = mapped_column(String(64), unique=True)
    unionid: Mapped[str | None] = mapped_column(String(64), index=True)
    profile_completed: Mapped[int] = mapped_column(Integer, default=0)
    nickname: Mapped[str | None] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(String(500))


class CustomerAlipayAccount(Base, TimestampMixin):
    __tablename__ = "customer_alipay_accounts"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"), index=True)
    alipay_user_id: Mapped[str] = mapped_column(String(64), unique=True)
    alipay_union_id: Mapped[str | None] = mapped_column(String(64))
    profile_completed: Mapped[int] = mapped_column(Integer, default=0)
    nickname: Mapped[str | None] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(String(500))


class CustomerPreference(Base):
    __tablename__ = "customer_preferences"
    __table_args__ = (
        UniqueConstraint("customer_id", "category", "option_code"),
        Index("ix_cp_customer_score", "customer_id", "preference_score"),
        Index("ix_cp_category_customer", "category", "customer_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    category: Mapped[str] = mapped_column(String(50))
    option_code: Mapped[str] = mapped_column(String(50))
    option_name: Mapped[str | None] = mapped_column(String(50))
    preference_score: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.0000"))
    order_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CustomerFavoriteProduct(Base, TimestampMixin):
    """菜品收藏：每位顾客对同一菜品仅一条记录。"""

    __tablename__ = "customer_favorite_products"
    __table_args__ = (
        UniqueConstraint("customer_id", "product_id", name="uq_cfp_customer_product"),
        Index("ix_cfp_customer_store", "customer_id", "store_id"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), index=True)


class CustomerLifecycle(Base, TimestampMixin):
    """顾客生命周期 + RFM 夜批快照（店长筛选真源）。"""

    __tablename__ = "customer_lifecycle"
    __table_args__ = (
        UniqueConstraint("customer_id", name="uk_cl_customer"),
        Index("idx_cl_lifecycle", "lifecycle_code"),
        Index("idx_cl_rfm", "rfm_segment"),
        Index("idx_cl_silence", "silence_days"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id", ondelete="CASCADE")
    )
    lifecycle_code: Mapped[str] = mapped_column(String(20), default="GUEST")
    rfm_segment: Mapped[str | None] = mapped_column(String(32), default=None)
    r_score: Mapped[int | None] = mapped_column(Integer, default=None)
    f_score: Mapped[int | None] = mapped_column(Integer, default=None)
    m_score: Mapped[int | None] = mapped_column(Integer, default=None)
    silence_days: Mapped[int] = mapped_column(Integer, default=0)
    order_count_90d: Mapped[int] = mapped_column(Integer, default=0)
    pay_amount_90d: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvoiceRequest(Base, TimestampMixin):
    """电子发票开票申请：顾客提交，后台标记开票/驳回。"""

    __tablename__ = "invoice_requests"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_invoice_order"),
        Index("ix_invoice_requests_store_status", "store_id", "status"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"), index=True)
    order_no: Mapped[str] = mapped_column(String(40))
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("customers.id"))
    title_type: Mapped[str] = mapped_column(String(10), default="PERSON")  # PERSON/COMPANY
    title: Mapped[str] = mapped_column(String(200))
    tax_no: Mapped[str | None] = mapped_column(String(50), default=None)
    email: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)  # PENDING/ISSUED/REJECTED
    reject_reason: Mapped[str | None] = mapped_column(String(255), default=None)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class QueueTicket(Base, TimestampMixin):
    """排队取号：按门店按天编号，状态机 WAITING→CALLED→SEATED，可取消。"""

    __tablename__ = "queue_tickets"
    __table_args__ = (
        UniqueConstraint("store_id", "issue_date", "number", name="uq_queue_store_date_number"),
        Index("ix_queue_store_status", "store_id", "status"),
        Index("ix_queue_customer", "customer_id", "issue_date"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("customers.id"))
    ticket_no: Mapped[str] = mapped_column(String(20))  # 展示号，如 A08
    number: Mapped[int] = mapped_column(Integer, default=1)
    issue_date: Mapped[date] = mapped_column(Date)
    people_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="WAITING", index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    called_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    seated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

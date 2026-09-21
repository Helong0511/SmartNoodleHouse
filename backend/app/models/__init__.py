from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Boolean, Computed, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, Time, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(255))


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    module: Mapped[str] = mapped_column(String(50))


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("roles.id"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("permissions.id"), primary_key=True, index=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(100))
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("roles.id"), index=True)
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None, index=True)
    status: Mapped[int] = mapped_column(Integer, default=1)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_version: Mapped[int] = mapped_column(Integer, default=0)

    role: Mapped["Role"] = relationship("Role")


class OperationLog(Base):
    __tablename__ = "operation_logs"
    __table_args__ = (
        Index("ix_operation_logs_created_at_user_id", "created_at", "user_id"),
        Index("ix_operation_logs_category_created_at", "category", "created_at"),
        Index("ix_operation_logs_created_at_store_id", "created_at", "store_id"),
        Index("ix_operation_logs_created_at_module", "created_at", "module"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(100))
    module: Mapped[str] = mapped_column(String(50))
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON)
    # 审计分类：STAFF/STORE_ADMIN/CASHIER/KITCHEN/SYSTEM/CUSTOMER/DANGER/INTRUSION
    category: Mapped[str | None] = mapped_column(String(20), default="STAFF", comment="审计分类")
    # 门店归属：方便按门店过滤；NULL=全局/未绑定（如登录失败、跨店系统操作）
    store_id: Mapped[int | None] = mapped_column(BigInteger, default=None, comment="操作关联门店")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Store(Base, TimestampMixin):
    __tablename__ = "stores"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_no: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(20))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), default=None)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), default=None)
    status: Mapped[int] = mapped_column(Integer, default=1)


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


class MemberLevel(Base):
    __tablename__ = "member_levels"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50))
    min_points: Mapped[int] = mapped_column(Integer, default=0)
    discount_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1.0000"))


class CouponTemplate(Base):
    __tablename__ = "coupon_templates"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # NULL=总部/全店可见；非空=仅该门店
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None, index=True)
    name: Mapped[str] = mapped_column(String(100))
    coupon_type: Mapped[str] = mapped_column(String(20), default="FIXED")
    discount_value: Mapped[Decimal] = mapped_column(Numeric(10, 4))
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
    # 智能推荐合规：1=开启个性化推荐（默认），0=关闭（退回非个性化热销榜，不采集行为）
    recommend_opt_in: Mapped[int] = mapped_column(Integer, default=1)
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


class ProductCategory(Base, TimestampMixin):
    __tablename__ = "product_categories"
    __table_args__ = (
        UniqueConstraint("store_id", "category_no", name="uk_cat_store_no"),
        Index("ix_pc_sort_status_store", "sort_order", "status", "store_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    # 门店前缀编号，如 SH01-Mian；与商品/规格编码规则一致
    category_no: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=1)
    # 须先点过的分类 id 列表（OR）；空=无限制。例：【加料】依赖【面】
    require_category_ids: Mapped[list | None] = mapped_column(JSON, default=None)
    # 推荐差异化配置：{"weights":{"content":0.45,"cf":0.25,"popular":0.15,"novel":0.15},
    #   "repurchase_window_days":7}；NULL=继承全局默认（system_settings）
    recommend_config: Mapped[dict | None] = mapped_column(JSON, default=None)


class CategorySortSchedule(Base, TimestampMixin):
    """门店分类分时段排序方案"""
    __tablename__ = "category_sort_schedules"
    __table_args__ = (Index("ix_css_priority_status_store", "priority", "status", "store_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    name: Mapped[str] = mapped_column(String(100))
    start_time: Mapped[object] = mapped_column(Time)
    end_time: Mapped[object] = mapped_column(Time)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=1)


class CategorySortScheduleItem(Base):
    __tablename__ = "category_sort_schedule_items"
    __table_args__ = (
        UniqueConstraint("schedule_id", "category_id"),
        Index("ix_cssi_schedule_sort", "schedule_id", "sort_order"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    schedule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("category_sort_schedules.id", ondelete="CASCADE")
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("product_categories.id", ondelete="CASCADE"), index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("store_id", "product_no"),
        Index("ix_p_sort_status_store", "sort_order", "status", "store_id"),
        Index("ix_p_category_sort_status", "category_id", "sort_order", "status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product_categories.id"))
    product_no: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    main_image: Mapped[str | None] = mapped_column(String(500))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20), default="ON_SALE")
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    is_hot: Mapped[bool] = mapped_column(Boolean, default=False)
    no_member_discount: Mapped[bool] = mapped_column(Boolean, default=False)
    no_cooking: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    sales_count: Mapped[int] = mapped_column(Integer, default=0)
    stock: Mapped[int] = mapped_column(Integer, default=999)
    box_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    min_order_count: Mapped[int] = mapped_column(Integer, default=1)
    # NULL=继承所属分类默认；[]/有值=商品自身配置（与 require_product_ids 合并 OR）
    require_category_ids: Mapped[list | None] = mapped_column(JSON, default=None)
    require_product_ids: Mapped[list | None] = mapped_column(JSON, default=None)


class ProductImage(Base):
    __tablename__ = "product_images"
    __table_args__ = (Index("ix_pi_product_sort", "product_id", "sort_order"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"))
    image_url: Mapped[str] = mapped_column(String(500))
    image_type: Mapped[str] = mapped_column(String(20), default="DETAIL")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OptionGroup(Base, TimestampMixin):
    __tablename__ = "option_groups"
    # store_scope = IFNULL(store_id, 0) STORED：让 store_id=NULL 的总部共享行也参与 UNIQUE
    # （MySQL/SQLite 唯一索引对 NULL 不生效，必须用 GENERATED 列把 NULL 归一为 0）
    __table_args__ = (UniqueConstraint("store_scope", "code", name="uk_og_store_code"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # NULL=总部共享（全店可用）；非空=仅该门店可改/可绑本店商品
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("stores.id"), default=None, index=True)
    name: Mapped[str] = mapped_column(String(50))
    code: Mapped[str] = mapped_column(String(64))
    selection_type: Mapped[str] = mapped_column(String(20), default="SINGLE")
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=1)
    # GENERATED 列：把 NULL store_id 归一为 0，配合 UNIQUE (store_scope, code) 让总部 code 也唯一
    store_scope: Mapped[int] = mapped_column(
        BigInteger,
        Computed("IFNULL(store_id, 0)"),
        nullable=False,
        comment="唯一约束用：NULL store→0",
    )


class Option(Base, TimestampMixin):
    __tablename__ = "options"
    __table_args__ = (UniqueConstraint("group_id", "code"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("option_groups.id"))
    name: Mapped[str] = mapped_column(String(50))
    code: Mapped[str] = mapped_column(String(80))
    extra_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[int] = mapped_column(Integer, default=1)


class ProductOptionGroup(Base):
    __tablename__ = "product_option_groups"
    __table_args__ = (UniqueConstraint("product_id", "group_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"))
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("option_groups.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class StoreProductStock(Base, TimestampMixin):
    """分店库存与上架覆盖；无记录则回退品牌商品 stock/status"""
    __tablename__ = "store_product_stocks"
    __table_args__ = (UniqueConstraint("store_id", "product_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), index=True)
    stock: Mapped[int] = mapped_column(Integer, default=999)
    status: Mapped[int] = mapped_column(Integer, default=1)  # 1在售 0本店停售


class StoreSetting(Base):
    """门店业务参数覆盖（覆盖全局 system_settings 同名键）"""
    __tablename__ = "store_settings"
    __table_args__ = (UniqueConstraint("store_id", "setting_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id", ondelete="CASCADE"))
    setting_key: Mapped[str] = mapped_column(String(100))
    setting_value: Mapped[str] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id"), index=True)
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
    requested_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_remark: Mapped[str | None] = mapped_column(String(500))
    refund_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("payment_refunds.id"))
    fail_reason: Mapped[str | None] = mapped_column(String(255))


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
    __table_args__ = (Index("ix_ve_camera_event_time", "camera_id", "event_time"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    visitor_no: Mapped[str] = mapped_column(String(64))
    camera_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cameras.id"))
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


class SystemSetting(Base):
    __tablename__ = "system_settings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    setting_key: Mapped[str] = mapped_column(String(100), unique=True)
    setting_value: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DeliveryProductMap(Base, TimestampMixin):
    """平台菜品 ↔ 本系统商品"""

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


class PlatformCategoryMap(Base, TimestampMixin):
    """本地商品分类 ↔ 外卖平台分类映射。store_id=NULL 表示总部默认映射。"""

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
    # (platform, platform_refund_id) 唯一：防止重复回调/并发请求创建多条退款台账
    # platform_refund_id 为 NULL 时不受约束（平台未给 refund_id 的退款仍可入账）
    __table_args__ = (UniqueConstraint("platform", "platform_refund_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    delivery_order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("delivery_orders.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20))
    platform_refund_id: Mapped[str | None] = mapped_column(String(64))
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
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentCallbackReceipt(Base):
    __tablename__ = "payment_callback_receipts"
    __table_args__ = (UniqueConstraint("provider", "receipt_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(20))
    receipt_key: Mapped[str] = mapped_column(String(128))
    payment_no: Mapped[str | None] = mapped_column(String(32))
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
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class ProductTasteProfile(Base, TimestampMixin):
    """产品口味画像：自动推导（AUTO）与店长微调（MANUAL）共存。

    dimensions 例：{"spicy":2,"sweetness":0,"sourness":1,"numbing":0,"temperature":1}
    tags 例：["劲道","酸辣","牛肉","冰饮"]；manual_overrides 记录被人工接管的键，
    AUTO 重算时跳过这些键（dimensions 的键名或 tags 字面量）。
    """

    __tablename__ = "product_taste_profiles"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), unique=True
    )
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"), index=True)
    dimensions: Mapped[dict | None] = mapped_column(JSON, default=None)
    tags: Mapped[list | None] = mapped_column(JSON, default=None)
    source: Mapped[str] = mapped_column(String(10), default="AUTO")
    manual_overrides: Mapped[list | None] = mapped_column(JSON, default=None)
    derived_from: Mapped[str | None] = mapped_column(String(200), default=None)


class CustomerTasteProfile(Base):
    """顾客多维口味画像（CATEGORY/PRODUCT/TASTE_TAG），行为事件增量聚合。

    与规格选项维度的 CustomerPreference 并存：本表由行为流驱动，
    CustomerPreference 仍由已付订单全量重算，两者互不写入。
    """

    __tablename__ = "customer_taste_profiles"
    __table_args__ = (
        UniqueConstraint("customer_id", "dim_type", "target_key", name="uq_ctp_customer_dim_target"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    # CATEGORY=品类亲和 / PRODUCT=单品偏好 / TASTE_TAG=口味标签偏好
    dim_type: Mapped[str] = mapped_column(String(16))
    # CATEGORY→category_id 字符串；PRODUCT→product_id 字符串；TASTE_TAG→标签字面量
    target_key: Mapped[str] = mapped_column(String(100))
    target_name: Mapped[str | None] = mapped_column(String(100))
    score: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0.0000"))
    raw_weight: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0.0000"))
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CustomerBehaviorEvent(Base):
    """顾客行为事件（曝光/点击/加购/移除/复购/评价等），推荐与 A/B 报表的数据源。

    event_id 为客户端生成的幂等 UUID；不存 PII（手机号/昵称等），按留存期清理。
    """

    __tablename__ = "customer_behavior_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_cbe_event_id"),
        Index("ix_cbe_created_at_customer_id", "created_at", "customer_id"),
        Index("ix_cbe_created_at_event_type_store", "created_at", "event_type", "store_id"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    # 客户端生成的幂等 UUID，重复上报靠它去重
    event_id: Mapped[str] = mapped_column(String(64))
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id", ondelete="CASCADE")
    )
    store_id: Mapped[int | None] = mapped_column(BigInteger)
    # EXPOSE/CLICK/CART_ADD/CART_REMOVE/ORDER_PAID/REORDER/FEEDBACK
    event_type: Mapped[str] = mapped_column(String(20))
    # menu/recommend/cart/detail/settle/orders
    scene: Mapped[str | None] = mapped_column(String(20))
    product_id: Mapped[int | None] = mapped_column(BigInteger)
    category_id: Mapped[int | None] = mapped_column(BigInteger)
    # 命中的 A/B 实验与桶，格式 "reco_algo:v2"，便于聚合归因
    ab_bucket: Mapped[str | None] = mapped_column(String(80))
    payload: Mapped[dict | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ProductFeedback(Base, TimestampMixin):
    """轻量评价：已支付订单单品可评一次（标签点选，非星级长评）。"""

    __tablename__ = "product_feedbacks"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id", "customer_id", name="uq_pf_order_product_customer"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"), index=True)
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    store_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    # 白名单标签，如 ["TOO_SPICY","DISLIKE"] / ["TASTY"]
    verdicts: Mapped[list | None] = mapped_column(JSON, default=None)
    # POSITIVE/NEGATIVE/NEUTRAL
    sentiment: Mapped[str] = mapped_column(String(10), default="NEUTRAL")
    remark: Mapped[str | None] = mapped_column(String(255))


class AbExperiment(Base, TimestampMixin):
    """A/B 实验定义。buckets 例 [{"code":"v1","traffic":50,"params":{...}},
    {"code":"v2","traffic":50,"params":{...}}]；traffic 总和为桶内比例。"""

    __tablename__ = "ab_experiments"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    experiment_key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    # DRAFT/RUNNING/PAUSED
    status: Mapped[str] = mapped_column(String(10), default="DRAFT")
    # 参与实验的流量百分比 0-100；其余走 default 桶
    traffic_percent: Mapped[int] = mapped_column(Integer, default=0)
    default_bucket: Mapped[str] = mapped_column(String(40), default="v1")
    buckets: Mapped[list | None] = mapped_column(JSON, default=None)
    description: Mapped[str | None] = mapped_column(String(255))


class AbAssignment(Base):
    """顾客在某实验中的稳定分桶记录（幂等 upsert，保证桶位恒定）。"""

    __tablename__ = "ab_assignments"
    __table_args__ = (
        UniqueConstraint("experiment_key", "customer_id", name="uq_aba_experiment_customer"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    experiment_key: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    bucket: Mapped[str] = mapped_column(String(40))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

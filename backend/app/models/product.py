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

class ProductCategory(Base, TimestampMixin):
    __tablename__ = "product_categories"
    __table_args__ = (
        UniqueConstraint("store_id", "category_no", name="uk_cat_store_no"),
        Index("ix_pc_sort_status_store", "sort_order", "status", "store_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    # 门店前缀编号，如 SH01-Mian；与餐品/规格编码规则一致
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
    auto_sort: Mapped[int] = mapped_column(Integer, default=0)
    auto_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


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
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)
    status: Mapped[str] = mapped_column(String(20), default="ON_SALE")
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    is_hot: Mapped[bool] = mapped_column(Boolean, default=False)
    no_member_discount: Mapped[bool] = mapped_column(Boolean, default=False)
    no_cooking: Mapped[bool] = mapped_column(Boolean, default=False)
    # ALWAYS / DATE_RANGE / ANNUAL_MD / BIRTHDAY_TODAY
    visibility_rule: Mapped[str] = mapped_column(String(20), default="ALWAYS")
    # SOLAR=公历 / LUNAR=农历（ANNUAL_MD 每年按历法月日匹配）
    visibility_calendar: Mapped[str] = mapped_column(String(10), default="SOLAR")
    visible_from: Mapped[date | None] = mapped_column(Date, default=None)
    visible_to: Mapped[date | None] = mapped_column(Date, default=None)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    sales_count: Mapped[int] = mapped_column(Integer, default=0)
    stock: Mapped[int] = mapped_column(Integer, default=999)
    box_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    min_order_count: Mapped[int] = mapped_column(Integer, default=1)
    # NULL=继承所属分类默认；[]/有值=餐品自身配置（与 require_product_ids 合并 OR）
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
    # NULL=总部共享（全店可用）；非空=仅该门店可改/可绑本店餐品
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
    """分店库存与上架覆盖；无记录则回退品牌餐品 stock/status"""
    __tablename__ = "store_product_stocks"
    __table_args__ = (UniqueConstraint("store_id", "product_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stores.id"))
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id", ondelete="CASCADE"), index=True)
    stock: Mapped[int] = mapped_column(Integer, default=999)
    status: Mapped[int] = mapped_column(Integer, default=1)  # 1在售 0本店停售


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

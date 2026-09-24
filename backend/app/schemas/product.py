from decimal import Decimal

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    store_id: int | None = None
    category_no: str | None = None
    name: str
    description: str | None = None
    sort_order: int = 0
    status: int = 1
    # 须先点过的分类 id 列表（OR：购物车有其中任一分类即可）；空=无限制
    require_category_ids: list[int] | None = None


class CategoryUpdate(BaseModel):
    category_no: str | None = None
    name: str | None = None
    description: str | None = None
    sort_order: int | None = None
    status: int | None = None
    # 一般不改门店；超管可迁移
    store_id: int | None = None
    require_category_ids: list[int] | None = None


class CategoryOut(BaseModel):
    id: int
    store_id: int
    category_no: str | None = None
    name: str
    description: str | None
    sort_order: int
    status: int
    require_category_ids: list[int] | None = None

    model_config = {"from_attributes": True}


class CategoryScheduleItemIn(BaseModel):
    category_id: int
    sort_order: int = 0


class CategoryScheduleCreate(BaseModel):
    store_id: int | None = None
    name: str
    start_time: str  # HH:MM 或 HH:MM:SS
    end_time: str
    priority: int = 0
    status: int = 1
    auto_sort: int = 0
    items: list[CategoryScheduleItemIn] = []


class CategoryScheduleUpdate(BaseModel):
    name: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    priority: int | None = None
    status: int | None = None
    auto_sort: int | None = None
    items: list[CategoryScheduleItemIn] | None = None


class ProductCreate(BaseModel):
    store_id: int | None = None
    category_id: int
    product_no: str
    name: str
    description: str | None = None
    price: Decimal = Field(gt=0, description="售价须大于 0")
    original_price: Decimal | None = Field(default=None, ge=0, description="划线价须 ≥ 0")
    status: str = "ON_SALE"
    is_recommended: bool = False
    is_hot: bool = False
    no_member_discount: bool = False
    no_cooking: bool = False
    sort_order: int = 0
    stock: int = Field(default=999, ge=0, description="库存须 ≥ 0")
    box_price: Decimal = Field(default=Decimal("0.00"), ge=0, description="餐盒/打包费（单价）")
    main_image: str | None = None
    option_group_ids: list[int] = []
    # None=创建时自动带入所属分类默认；[]=明确无限制；有值=商品自身配置
    require_category_ids: list[int] | None = None
    require_product_ids: list[int] | None = None


class ProductUpdate(BaseModel):
    store_id: int | None = None
    category_id: int | None = None
    name: str | None = None
    description: str | None = None
    price: Decimal | None = Field(default=None, gt=0, description="售价须大于 0")
    original_price: Decimal | None = Field(default=None, ge=0, description="划线价须 ≥ 0")
    status: str | None = None
    is_recommended: bool | None = None
    is_hot: bool | None = None
    no_member_discount: bool | None = None
    no_cooking: bool | None = None
    sort_order: int | None = None
    stock: int | None = Field(default=None, ge=0, description="库存须 ≥ 0")
    box_price: Decimal | None = Field(default=None, ge=0)
    main_image: str | None = None
    option_group_ids: list[int] | None = None
    require_category_ids: list[int] | None = None
    require_product_ids: list[int] | None = None


class ProductOut(BaseModel):
    id: int
    store_id: int
    category_id: int
    product_no: str
    name: str
    description: str | None
    main_image: str | None
    price: Decimal
    original_price: Decimal | None
    status: str
    is_recommended: bool
    is_hot: bool
    no_member_discount: bool
    no_cooking: bool
    sort_order: int
    sales_count: int
    require_category_ids: list[int] | None = None
    require_product_ids: list[int] | None = None

    model_config = {"from_attributes": True}


class OptionGroupOut(BaseModel):
    id: int
    store_id: int | None = None
    name: str
    code: str
    selection_type: str
    is_required: bool
    sort_order: int
    options: list["OptionOut"] = []

    model_config = {"from_attributes": True}


class OptionOut(BaseModel):
    id: int
    group_id: int
    name: str
    code: str
    extra_price: Decimal
    sort_order: int
    is_default: bool = False
    status: int

    model_config = {"from_attributes": True}


class MenuCopyRequest(BaseModel):
    """跨门店复制分类/规格组/商品到目标店（新建副本）。"""
    target_store_id: int = Field(..., gt=0)
    category_ids: list[int] = []
    option_group_ids: list[int] = []
    product_ids: list[int] = []
    # 目标店已有同 product_no：skip=跳过；rename=自动改编号后创建
    on_product_conflict: str = Field(default="skip", pattern="^(skip|rename)$")
    # 规格管理页：将总部共享也克隆为目标店私有副本；商品复制时默认 false（共享组直接沿用）
    clone_shared_option_groups: bool = False


class OptionGroupCreate(BaseModel):
    name: str
    code: str
    selection_type: str = "SINGLE"
    is_required: bool = True
    sort_order: int = 0
    # 超管可指定；门店账号服务端强制为本店
    store_id: int | None = None


class OptionGroupUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    selection_type: str | None = None
    is_required: bool | None = None
    sort_order: int | None = None
    status: int | None = None
    store_id: int | None = None


class OptionCreate(BaseModel):
    group_id: int
    name: str
    code: str
    extra_price: Decimal = Field(default=Decimal("0.00"), ge=0, description="加价须 ≥ 0")
    sort_order: int = 0
    is_default: bool = False


class OptionUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    extra_price: Decimal | None = Field(default=None, ge=0, description="加价须 ≥ 0")
    sort_order: int | None = None
    is_default: bool | None = None
    status: int | None = None

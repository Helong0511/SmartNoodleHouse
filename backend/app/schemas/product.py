from decimal import Decimal

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    store_id: int | None = None
    name: str
    description: str | None = None
    sort_order: int = 0
    status: int = 1


class CategoryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    sort_order: int | None = None
    status: int | None = None
    # 一般不改门店；超管可迁移
    store_id: int | None = None


class CategoryOut(BaseModel):
    id: int
    store_id: int
    name: str
    description: str | None
    sort_order: int
    status: int

    model_config = {"from_attributes": True}


class ProductCreate(BaseModel):
    store_id: int | None = None
    category_id: int
    product_no: str
    name: str
    description: str | None = None
    price: Decimal = Field(gt=0, description="售价须大于 0")
    original_price: Decimal | None = None
    status: str = "ON_SALE"
    is_recommended: bool = False
    is_hot: bool = False
    no_member_discount: bool = False
    sort_order: int = 0
    stock: int = 999
    box_price: Decimal = Field(default=Decimal("0.00"), ge=0, description="餐盒/打包费（单价）")
    main_image: str | None = None
    option_group_ids: list[int] = []


class ProductUpdate(BaseModel):
    store_id: int | None = None
    category_id: int | None = None
    name: str | None = None
    description: str | None = None
    price: Decimal | None = None
    original_price: Decimal | None = None
    status: str | None = None
    is_recommended: bool | None = None
    is_hot: bool | None = None
    no_member_discount: bool | None = None
    sort_order: int | None = None
    stock: int | None = None
    box_price: Decimal | None = None
    main_image: str | None = None
    option_group_ids: list[int] | None = None


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
    sort_order: int
    sales_count: int

    model_config = {"from_attributes": True}


class OptionGroupOut(BaseModel):
    id: int
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
    status: int

    model_config = {"from_attributes": True}


class OptionGroupCreate(BaseModel):
    name: str
    code: str
    selection_type: str = "SINGLE"
    is_required: bool = True
    sort_order: int = 0


class OptionGroupUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    selection_type: str | None = None
    is_required: bool | None = None
    sort_order: int | None = None
    status: int | None = None


class OptionCreate(BaseModel):
    group_id: int
    name: str
    code: str
    extra_price: Decimal = Decimal("0.00")
    sort_order: int = 0


class OptionUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    extra_price: Decimal | None = None
    sort_order: int | None = None
    status: int | None = None

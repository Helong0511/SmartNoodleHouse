from decimal import Decimal

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    name: str
    description: str | None = None
    sort_order: int = 0
    status: int = 1


class CategoryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    sort_order: int | None = None
    status: int | None = None


class CategoryOut(BaseModel):
    id: int
    name: str
    description: str | None
    sort_order: int
    status: int

    model_config = {"from_attributes": True}


class ProductCreate(BaseModel):
    category_id: int
    product_no: str
    name: str
    description: str | None = None
    price: Decimal
    original_price: Decimal | None = None
    status: str = "ON_SALE"
    is_recommended: bool = False
    is_hot: bool = False
    sort_order: int = 0
    option_group_ids: list[int] = []


class ProductUpdate(BaseModel):
    category_id: int | None = None
    name: str | None = None
    description: str | None = None
    price: Decimal | None = None
    original_price: Decimal | None = None
    status: str | None = None
    is_recommended: bool | None = None
    is_hot: bool | None = None
    sort_order: int | None = None
    option_group_ids: list[int] | None = None


class ProductOut(BaseModel):
    id: int
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


class OptionCreate(BaseModel):
    group_id: int
    name: str
    code: str
    extra_price: Decimal = Decimal("0.00")
    sort_order: int = 0

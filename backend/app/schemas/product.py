from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.birthday import VALID_CALENDARS
from app.utils.product_visibility import (
    VALID_VISIBILITY_RULES,
    normalize_visibility_calendar,
    normalize_visibility_rule,
)


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_id: int | None = None
    category_no: str | None = Field(default=None, max_length=32)
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
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
    model_config = ConfigDict(extra="forbid")

    store_id: int | None = None
    category_id: int
    product_no: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal = Field(gt=0, description="售价须大于 0")
    original_price: Decimal | None = Field(default=None, ge=0, description="划线价须 ≥ 0")
    status: str = Field(default="ON_SALE", pattern=r"^(ON_SALE|OFF_SALE|SOLD_OUT)$")
    is_recommended: bool = False
    is_hot: bool = False
    no_member_discount: bool = False
    no_cooking: bool = False
    visibility_rule: str = Field(default="ALWAYS", description="ALWAYS/DATE_RANGE/ANNUAL_MD/BIRTHDAY_TODAY")
    visibility_calendar: str = Field(default="SOLAR", description="SOLAR=公历 / LUNAR=农历")
    visible_from: date | None = None
    visible_to: date | None = None
    sort_order: int = 0
    stock: int = Field(default=999, ge=0, description="库存须 ≥ 0")
    box_price: Decimal = Field(default=Decimal("0.00"), ge=0, description="餐盒/打包费（单价）")
    main_image: str | None = Field(default=None, max_length=500)
    option_group_ids: list[int] = []
    # None=下单时自动带入所属分类默认；[]=明确无限制；有值=餐品自身配置
    require_category_ids: list[int] | None = None
    require_product_ids: list[int] | None = None

    @field_validator("visibility_rule")
    @classmethod
    def _norm_vis_create(cls, v: str) -> str:
        raw = str(v or "").strip().upper()
        if raw and raw not in VALID_VISIBILITY_RULES:
            raise ValueError("visibility_rule 无效")
        return normalize_visibility_rule(v)

    @field_validator("visibility_calendar")
    @classmethod
    def _norm_cal_create(cls, v: str) -> str:
        raw = str(v or "").strip().upper()
        if raw and raw not in VALID_CALENDARS:
            raise ValueError("visibility_calendar 须为 SOLAR 或 LUNAR")
        return normalize_visibility_calendar(v)

    @model_validator(mode="after")
    def _vis_dates_create(self):
        rule = normalize_visibility_rule(self.visibility_rule)
        if rule in ("DATE_RANGE", "ANNUAL_MD") and self.visible_from is None:
            raise ValueError("限时展示须填写 visible_from")
        if (
            self.visible_from
            and self.visible_to
            and rule == "DATE_RANGE"
            and self.visible_to < self.visible_from
        ):
            raise ValueError("visible_to 不能早于 visible_from")
        return self


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_id: int | None = None
    category_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal | None = Field(default=None, gt=0, description="售价须大于 0")
    original_price: Decimal | None = Field(default=None, ge=0, description="划线价须 ≥ 0")
    status: str | None = Field(default=None, pattern=r"^(ON_SALE|OFF_SALE|SOLD_OUT)$")
    is_recommended: bool | None = None
    is_hot: bool | None = None
    no_member_discount: bool | None = None
    no_cooking: bool | None = None
    visibility_rule: str | None = None
    visibility_calendar: str | None = None
    visible_from: date | None = None
    visible_to: date | None = None
    sort_order: int | None = None
    stock: int | None = Field(default=None, ge=0, description="库存须 ≥ 0")
    box_price: Decimal | None = Field(default=None, ge=0)
    main_image: str | None = Field(default=None, max_length=500)
    option_group_ids: list[int] | None = None
    require_category_ids: list[int] | None = None
    require_product_ids: list[int] | None = None

    @field_validator("visibility_rule")
    @classmethod
    def _norm_vis_update(cls, v: str | None) -> str | None:
        if v is None:
            return None
        rule = normalize_visibility_rule(v)
        raw = str(v).strip().upper()
        if raw and raw not in VALID_VISIBILITY_RULES:
            raise ValueError("visibility_rule 无效")
        return rule

    @field_validator("visibility_calendar")
    @classmethod
    def _norm_cal_update(cls, v: str | None) -> str | None:
        if v is None:
            return None
        raw = str(v).strip().upper()
        if raw and raw not in VALID_CALENDARS:
            raise ValueError("visibility_calendar 须为 SOLAR 或 LUNAR")
        return normalize_visibility_calendar(v)

    @model_validator(mode="after")
    def _vis_dates_update(self):
        # 仅当本次请求显式改了规则或日期时校验，避免局部更新误伤
        fields = getattr(self, "model_fields_set", None) or set()
        if not fields.intersection(
            {"visibility_rule", "visibility_calendar", "visible_from", "visible_to"}
        ):
            return self
        rule = (
            normalize_visibility_rule(self.visibility_rule)
            if self.visibility_rule is not None
            else None
        )
        if (
            "visibility_rule" in fields
            and rule in ("DATE_RANGE", "ANNUAL_MD")
            and self.visible_from is None
        ):
            raise ValueError("限时展示须填写 visible_from")
        if (
            self.visible_from
            and self.visible_to
            and rule == "DATE_RANGE"
            and self.visible_to < self.visible_from
        ):
            raise ValueError("visible_to 不能早于 visible_from")
        return self


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
    visibility_rule: str = "ALWAYS"
    visibility_calendar: str = "SOLAR"
    visible_from: date | None = None
    visible_to: date | None = None
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
    """跨门店复制分类/规格组/餐品到目标店（新建副本）。"""
    target_store_id: int = Field(..., gt=0)
    category_ids: list[int] = []
    option_group_ids: list[int] = []
    product_ids: list[int] = []
    # 目标店已有同 product_no：skip=跳过；rename=自动改编号后创建
    on_product_conflict: str = Field(default="skip", pattern="^(skip|rename)$")
    # 规格管理页：将总部共享也克隆为目标店私有副本；餐品复制时默认 false（共享组直接沿用）
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

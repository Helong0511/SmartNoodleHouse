from decimal import Decimal

from pydantic import BaseModel, Field


class CouponTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    coupon_type: str = Field(default="FIXED", pattern="^(FIXED|PERCENT|VOUCHER)$")
    discount_value: Decimal = Field(..., gt=0)
    min_order_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    valid_days: int = Field(default=30, ge=1, le=365)
    status: int = Field(default=1, ge=0, le=1)
    # NULL=总部全店；门店账号创建时由后端强制为本店
    store_id: int | None = Field(default=None, description="NULL=总部模板；非空=门店模板")
    total_quota: int | None = Field(default=None, ge=0, description="发放总量上限；NULL=不限；0=禁止发放")
    # P2-4 仅对总部模板生效：NULL=全部门店可见；非空=仅列出门店可见
    store_whitelist: list[int] | None = Field(default=None, description="总部模板可见门店白名单；NULL=全部门店")
    schedule_mode: str = Field(default="ALWAYS", pattern="^(ALWAYS|ANNUAL_MD)$")
    schedule_calendar: str = Field(default="SOLAR", pattern="^(SOLAR|LUNAR)$")
    annual_start_month: int | None = Field(default=None, ge=1, le=12)
    annual_start_day: int | None = Field(default=None, ge=1, le=31)
    annual_end_month: int | None = Field(default=None, ge=1, le=12)
    annual_end_day: int | None = Field(default=None, ge=1, le=31)


class CouponTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    coupon_type: str | None = Field(default=None, pattern="^(FIXED|PERCENT|VOUCHER)$")
    discount_value: Decimal | None = Field(default=None, gt=0)
    min_order_amount: Decimal | None = Field(default=None, ge=0)
    valid_days: int | None = Field(default=None, ge=1, le=365)
    status: int | None = Field(default=None, ge=0, le=1)
    store_id: int | None = Field(default=None, description="仅超管可改；显式 null=改回总部")
    total_quota: int | None = Field(default=None, ge=0, description="发放总量上限；0=禁止发放；NULL=不限；修改不影响 remaining")
    store_whitelist: list[int] | None = Field(default=None, description="总部模板门店白名单；显式 null=恢复全部门店可见")
    schedule_mode: str | None = Field(default=None, pattern="^(ALWAYS|ANNUAL_MD)$")
    schedule_calendar: str | None = Field(default=None, pattern="^(SOLAR|LUNAR)$")
    annual_start_month: int | None = Field(default=None, ge=1, le=12)
    annual_start_day: int | None = Field(default=None, ge=1, le=31)
    annual_end_month: int | None = Field(default=None, ge=1, le=12)
    annual_end_day: int | None = Field(default=None, ge=1, le=31)


class CouponIssueRequest(BaseModel):
    customer_id: int = Field(..., gt=0)
    template_id: int = Field(..., gt=0)
    quantity: int = Field(default=1, ge=1, le=50, description="发放张数，单次最多 50")
    client_request_id: str | None = Field(
        default=None,
        max_length=128,
        description="客户端幂等键；同键重复发券只成功一次",
    )

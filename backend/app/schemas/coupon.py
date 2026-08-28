from decimal import Decimal

from pydantic import BaseModel, Field


class CouponTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    coupon_type: str = Field(default="FIXED", pattern="^(FIXED|PERCENT|VOUCHER)$")
    discount_value: Decimal = Field(..., gt=0)
    min_order_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    valid_days: int = Field(default=30, ge=1, le=365)
    status: int = Field(default=1, ge=0, le=1)


class CouponTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    coupon_type: str | None = Field(default=None, pattern="^(FIXED|PERCENT|VOUCHER)$")
    discount_value: Decimal | None = Field(default=None, gt=0)
    min_order_amount: Decimal | None = Field(default=None, ge=0)
    valid_days: int | None = Field(default=None, ge=1, le=365)
    status: int | None = Field(default=None, ge=0, le=1)


class CouponIssueRequest(BaseModel):
    customer_id: int = Field(..., gt=0)
    template_id: int = Field(..., gt=0)
    quantity: int = Field(default=1, ge=1, le=50, description="发放张数（优惠券/代金券通用）")

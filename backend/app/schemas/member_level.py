from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.core.response import money


class MemberLevelItemUpdate(BaseModel):
    id: int = Field(..., gt=0)
    min_points: int = Field(..., ge=0, le=10_000_000)
    discount_rate: Decimal = Field(..., gt=0, le=1)

    @field_validator("discount_rate", mode="before")
    @classmethod
    def coerce_discount_rate(cls, v):
        return money(v)


class MemberLevelsBatchUpdate(BaseModel):
    items: list[MemberLevelItemUpdate] = Field(..., min_length=1)
    recalculate: bool = Field(default=True, description="保存后按新门槛重算全部顾客等级")

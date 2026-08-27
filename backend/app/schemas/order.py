from decimal import Decimal

from pydantic import BaseModel, Field


class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(ge=1, le=99)
    option_ids: list[int] = []
    remark: str | None = None


class OrderCreate(BaseModel):
    store_id: int | None = None
    table_token: str | None = None
    customer_id: int | None = None
    items: list[OrderItemCreate]
    remark: str | None = None
    channel: str | None = None
    # 堂食默认；打包外带时计商品餐盒费 box_price
    need_pack: bool = False
    dining_type: str | None = Field(default=None, description="DINE_IN / TAKEAWAY，可与 need_pack 二选一")
    idempotency_key: str | None = None


class OrderStatusUpdate(BaseModel):
    """仅允许厨房/履约推进；禁止人工写入 PAID。"""

    status: str = Field(
        description="CONFIRMED/PREPARING/COOKING/READY/COMPLETED/CANCELLED",
        pattern=r"^(CONFIRMED|PREPARING|COOKING|READY|COMPLETED|CANCELLED)$",
    )


class PaymentCreate(BaseModel):
    provider: str = Field(description="WECHAT or ALIPAY")
    channel_type: str = "native"


class OrderOut(BaseModel):
    id: int
    order_no: str
    customer_id: int | None
    table_id: int | None
    store_id: int
    status: str
    subtotal: Decimal
    discount_amount: Decimal
    total_amount: Decimal
    payment_status: str
    payment_method: str | None
    channel: str | None
    remark: str | None

    model_config = {"from_attributes": True}

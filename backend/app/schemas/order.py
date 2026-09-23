from decimal import Decimal

from pydantic import BaseModel, Field

# 单行品项数量上限（宴会/团餐可能超过 99；购物车连点也可累加）
MAX_ORDER_ITEM_QUANTITY = 999


class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(ge=1, le=MAX_ORDER_ITEM_QUANTITY)
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
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    customer_coupon_id: int | None = Field(default=None, description="用户优惠券 ID（满减/折扣）")
    customer_voucher_id: int | None = Field(default=None, description="用户代金券 ID")
    # 余额支付：储值已含赠送，不可再叠会员折扣/券/代金券
    skip_promotions: bool = Field(
        default=False,
        description="True 时不计会员折扣、优惠券、代金券（余额支付场景）",
    )


class OrderStatusUpdate(BaseModel):
    """仅允许厨房/履约推进；禁止人工写入 PAID。"""

    status: str = Field(
        description="CONFIRMED/PREPARING/COOKING/READY/COMPLETED/CANCELLED",
        pattern=r"^(CONFIRMED|PREPARING|COOKING|READY|COMPLETED|CANCELLED)$",
    )
    force_takeover: bool = Field(
        default=False,
        description="订单已被他人认领时，设为 true 表示接管并继续推进状态",
    )


class PosOrderCreate(BaseModel):
    """门店收银台下单（员工操作，channel=POS）。"""

    store_id: int | None = None
    table_id: int | None = Field(default=None, description="堂食必选桌台 ID")
    items: list[OrderItemCreate]
    remark: str | None = None
    need_pack: bool = False
    dining_type: str | None = Field(default=None, description="DINE_IN / TAKEAWAY")
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    waive_packing_fee: bool = Field(default=False, description="收银员免收打包费（仅 POS）")


class PosAddItems(BaseModel):
    """收银台加菜（往已有待支付订单追加商品）。"""

    items: list[OrderItemCreate]
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128, description="加菜幂等键")


class PosCashPay(BaseModel):
    """收银台现金收款（支持分笔；可选实收金额以计算找零）。"""

    amount: Decimal | None = Field(default=None, gt=0, description="本笔入账金额，默认=待收余额")
    received_amount: Decimal | None = Field(default=None, ge=0, description="顾客实收现金")
    round_down: bool = Field(default=False, description="抹零（向下取整到元）")


class PosScanPay(BaseModel):
    """收银台扫顾客付款码（支持分笔；自动识别微信/支付宝）。"""

    auth_code: str = Field(min_length=6, description="顾客付款码（扫码枪录入）")
    amount: Decimal | None = Field(default=None, gt=0, description="本笔收款金额，默认=待收余额")
    provider: str | None = Field(default=None, description="可选；不传则按付款码自动识别")


class PaymentCreate(BaseModel):
    provider: str = Field(description="WECHAT / ALIPAY / BALANCE")
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

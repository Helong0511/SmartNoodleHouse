from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# 单行品项数量上限（宴会/团餐可能超过 99；购物车连点也可累加）
MAX_ORDER_ITEM_QUANTITY = 999


class OrderItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int
    quantity: int = Field(ge=1, le=MAX_ORDER_ITEM_QUANTITY)
    option_ids: list[int] = Field(default_factory=list, max_length=20)
    remark: str | None = Field(default=None, max_length=200)


class OrderCreate(BaseModel):
    """下单 body：扫码凭证字段为 table_token（桌台实体字段为 qr_token）。"""

    model_config = ConfigDict(extra="forbid")

    store_id: int | None = None
    table_token: str | None = Field(default=None, max_length=64, description="扫码桌台凭证（对应 DiningTable.qr_token）")
    customer_id: int | None = None
    items: list[OrderItemCreate] = Field(..., min_length=1, max_length=100)
    remark: str | None = Field(default=None, max_length=500)
    channel: str | None = Field(default=None, max_length=32)
    # 堂食默认；打包外带时计餐品餐盒费 box_price
    need_pack: bool = False
    dining_type: str | None = Field(
        default=None,
        description="DINE_IN / TAKEAWAY，可与 need_pack 二选一",
        pattern=r"^(DINE_IN|TAKEAWAY)$",
    )
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    # 同桌共享车结算快照：必须与服务端 party revision 完全一致。
    shared_cart_party_id: int | None = Field(default=None, gt=0)
    shared_cart_revision: int | None = Field(default=None, ge=1)
    customer_coupon_id: int | None = Field(default=None, description="用户优惠券 ID（满减/折扣）")
    customer_voucher_id: int | None = Field(default=None, description="用户代金券 ID")
    # 余额支付：储值已含赠送，不可再叠会员折扣/券/代金券
    skip_promotions: bool = Field(
        default=False,
        description="True 时不计会员折扣、优惠券、代金券（余额支付场景）",
    )
    # 预点单：打包单预约取餐时间（ISO 本地时间），仅 TAKEAWAY 有效
    scheduled_pickup_at: str | None = Field(
        default=None, max_length=32, description="预约取餐时间 YYYY-MM-DDTHH:mm:ss（仅打包单）"
    )
    use_social_benefit: bool = Field(
        default=False, description="结算使用公益身份折扣（与券互斥；与会员折取更优）"
    )
    social_rule_id: int | None = Field(default=None, description="指定公益规则 ID（可选）")


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

    model_config = ConfigDict(extra="forbid")

    store_id: int | None = None
    table_id: int | None = Field(default=None, description="堂食必选桌台 ID")
    customer_id: int | None = Field(default=None, description="可选：绑定会员后计折扣/积分/里程碑")
    items: list[OrderItemCreate] = Field(..., min_length=1, max_length=100)
    remark: str | None = Field(default=None, max_length=500)
    need_pack: bool = False
    dining_type: str | None = Field(
        default=None, description="DINE_IN / TAKEAWAY", pattern=r"^(DINE_IN|TAKEAWAY)$"
    )
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    waive_packing_fee: bool = Field(default=False, description="收银员免收打包费（仅 POS）")
    use_social_benefit: bool = Field(
        default=False, description="使用公益身份折扣（与券互斥；与会员折取更优）"
    )
    social_rule_id: int | None = Field(default=None, description="指定公益规则 ID")
    customer_coupon_id: int | None = Field(default=None, description="优惠券（与公益互斥）")
    customer_voucher_id: int | None = Field(default=None, description="代金券（与公益互斥）")


class PosBindCustomer(BaseModel):
    """待支付 POS 单绑定/更换会员（未收款前）。"""

    customer_id: int | None = Field(default=None, description="None 表示清除会员绑定")
    customer_coupon_id: int | None = Field(default=None, description="优惠券（满减/折扣，与公益互斥）")
    customer_voucher_id: int | None = Field(default=None, description="代金券（与公益互斥）")
    use_social_benefit: bool = Field(
        default=False, description="使用公益身份折扣（与券互斥；与会员折取更优）"
    )
    social_rule_id: int | None = Field(default=None, description="指定公益规则 ID（可选）")

class PosAddItems(BaseModel):
    """收银台加菜（往已有待支付订单追加餐品）。"""

    model_config = ConfigDict(extra="forbid")

    items: list[OrderItemCreate] = Field(..., min_length=1, max_length=100)
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
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(description="WECHAT / ALIPAY / BALANCE", pattern=r"^(WECHAT|ALIPAY|BALANCE)$")
    channel_type: str = Field(
        default="native",
        pattern=r"^(native|jsapi|miniprogram|qr|barcode|h5)$",
    )

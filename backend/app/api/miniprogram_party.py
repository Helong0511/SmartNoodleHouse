"""小程序同桌共享购物车：从 miniprogram 路由拆出，路径仍挂在 /api/miniprogram。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentCustomer, DbSession
from app.core.exceptions import AppException
from app.core.response import success, serialize_dt
from app.models import Customer, DiningTable, Order
from app.services.dining_session_service import DiningSessionService

router = APIRouter(prefix="/miniprogram", tags=["小程序"])

# ---- 同桌共享购物车 ----


class SharedCartAddBody(BaseModel):
    table_token: str | None = Field(default=None, max_length=64)
    table_id: int | None = Field(default=None, gt=0)
    product_id: int = Field(..., gt=0)
    quantity: int = Field(default=1, ge=1, le=999)
    option_ids: list[int] = Field(default_factory=list)
    option_text: str | None = Field(default=None, max_length=255)
    remark: str | None = Field(default=None, max_length=200)
    unit_price: str | None = Field(default=None, max_length=16)
    expected_revision: int | None = Field(default=None, ge=1)


class SharedCartUpdateBody(BaseModel):
    quantity: int = Field(..., ge=0, le=999)
    expected_revision: int | None = Field(default=None, ge=1)


class SharedCartClearBody(BaseModel):
    table_token: str | None = Field(default=None, max_length=64)
    table_id: int | None = Field(default=None, gt=0)
    expected_revision: int | None = Field(default=None, ge=1)


async def _resolve_table_for_shared_cart(
    db: AsyncSession,
    customer: Customer,
    *,
    table_token: str | None = None,
    table_id: int | None = None,
) -> DiningTable:
    from app.services.shared_cart_service import SharedCartService, is_takeaway_table
    from app.services.table_resolve import resolve_dining_table

    table = None
    token = (table_token or "").strip()
    if token:
        table = await resolve_dining_table(db, token)
        # 同时传入 token 与 table_id 时必须指向同一桌，避免客户端上下文串桌。
        if table and table_id and int(table.id) != int(table_id):
            raise AppException(40313, "桌台凭证与桌台编号不匹配")
    elif table_id:
        # table_id 是顺序主键，不是访问凭证。没有 QR token 时，必须证明当前顾客
        # 已有未过期 DiningSession 且该会话绑定同一门店/桌台，防止枚举 table_id 加入他桌。
        active = await DiningSessionService.get_active(db, int(customer.id))
        if not active or not active.table_id or int(active.table_id) != int(table_id):
            raise AppException(40313, "请先扫描该桌二维码并绑定桌台")
        table = await db.get(DiningTable, int(table_id))
        if table and (
            active.store_id is None or int(active.store_id) != int(table.store_id)
        ):
            raise AppException(40313, "当前顾客桌台会话与门店不匹配")
    else:
        active = await DiningSessionService.get_active(db, int(customer.id))
        if active and active.table_id:
            table = await db.get(DiningTable, int(active.table_id))
    if not table or int(table.status or 0) != 1:
        raise AppException(40404, "请先扫码绑定桌台")
    if is_takeaway_table(table):
        raise AppException(40060, "外带桌不支持共享购物车")
    return table


class TablePartyJoinBody(BaseModel):
    table_token: str | None = Field(default=None, max_length=64)
    table_id: int | None = Field(default=None, gt=0)


@router.post("/table-party/join")
async def join_table_party(
    body: TablePartyJoinBody,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """加入/创建本桌就餐局并返回共享车。"""
    from app.services.shared_cart_service import SharedCartService

    table = await _resolve_table_for_shared_cart(
        db, customer, table_token=body.table_token, table_id=body.table_id
    )
    payload = await SharedCartService.join_party(db, customer=customer, table=table)
    await db.commit()
    return success(payload or {"enabled": False, "items": []}, request=request)


@router.get("/table-party/cart")
async def get_shared_cart(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    table_token: str | None = None,
    table_id: int | None = None,
):
    from app.services.shared_cart_service import SharedCartService

    try:
        table = await _resolve_table_for_shared_cart(
            db, customer, table_token=table_token, table_id=table_id
        )
    except AppException as e:
        if e.code == 40060:
            return success(
                {"enabled": False, "items": [], "rules": SharedCartService.rules_payload()},
                request=request,
            )
        raise
    payload = await SharedCartService.get_cart_for_customer(db, customer=customer, table=table)
    await db.commit()
    return success(payload, request=request)


@router.post("/table-party/cart/items")
async def add_shared_cart_item(
    body: SharedCartAddBody, request: Request, db: DbSession, customer: CurrentCustomer
):
    from app.services.shared_cart_service import SharedCartService

    table = await _resolve_table_for_shared_cart(
        db, customer, table_token=body.table_token, table_id=body.table_id
    )
    payload = await SharedCartService.add_item(
        db,
        customer=customer,
        table=table,
        product_id=int(body.product_id),
        quantity=int(body.quantity),
        option_ids=list(body.option_ids or []),
        option_text=body.option_text,
        remark=body.remark,
        unit_price=body.unit_price,
        expected_revision=body.expected_revision,
    )
    await db.commit()
    return success(payload, request=request)


@router.patch("/table-party/cart/items/{item_id}")
async def update_shared_cart_item(
    item_id: int,
    body: SharedCartUpdateBody,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    from app.services.shared_cart_service import SharedCartService

    payload = await SharedCartService.update_item(
        db,
        customer=customer,
        item_id=int(item_id),
        quantity=int(body.quantity),
        expected_revision=body.expected_revision,
    )
    await db.commit()
    return success(payload, request=request)


@router.delete("/table-party/cart/items/{item_id}")
async def delete_shared_cart_item(
    item_id: int,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    expected_revision: int | None = None,
):
    from app.services.shared_cart_service import SharedCartService

    payload = await SharedCartService.remove_item(
        db,
        customer=customer,
        item_id=int(item_id),
        expected_revision=expected_revision,
    )
    await db.commit()
    return success(payload, request=request)


@router.post("/table-party/cart/clear")
async def clear_shared_cart(
    body: SharedCartClearBody, request: Request, db: DbSession, customer: CurrentCustomer
):
    from app.services.shared_cart_service import SharedCartService

    table = await _resolve_table_for_shared_cart(
        db, customer, table_token=body.table_token, table_id=body.table_id
    )
    payload = await SharedCartService.clear_cart(
        db,
        customer=customer,
        table=table,
        expected_revision=body.expected_revision,
    )
    await db.commit()
    return success(payload, request=request)


@router.get("/table-party/orders")
async def list_table_party_orders(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    table_token: str | None = None,
    table_id: int | None = None,
):
    """本桌进行中订单聚合（全桌已下单，不只当前顾客）。"""
    from app.services.shared_cart_service import SharedCartService

    table = await _resolve_table_for_shared_cart(
        db, customer, table_token=table_token, table_id=table_id
    )
    if not await SharedCartService.is_enabled(db, int(table.store_id)):
        return success(
            {
                "table_id": int(table.id),
                "party_id": None,
                "items": [],
                "count": 0,
                "enabled": False,
            },
            request=request,
        )
    party = await SharedCartService.get_open_party(
        db, store_id=int(table.store_id), table_id=int(table.id)
    )
    # 没有活动 party 时不应仅凭静态桌台 QR 凭证读取整桌历史/进行中订单。
    if not party:
        return success(
            {
                "table_id": int(table.id),
                "party_id": None,
                "items": [],
                "count": 0,
                "enabled": True,
            },
            request=request,
        )
    await SharedCartService._require_member(
        db, party=party, customer_id=int(customer.id)
    )
    active = ("PAID", "CONFIRMED", "PREPARING", "COOKING", "READY")
    q = (
        select(Order)
        .where(
            Order.store_id == int(table.store_id),
            Order.table_id == int(table.id),
            Order.status.in_(active),
        )
        .order_by(Order.id.desc())
        .limit(30)
    )
    rows = list((await db.execute(q)).scalars().all())
    items = [
        {
            "id": int(o.id),
            "order_no": o.order_no,
            "status": o.status,
            "status_label": o.status,
            "total_amount": str(o.total_amount),
            "created_at": serialize_dt(o.created_at) if o.created_at else None,
        }
        for o in rows
    ]
    return success(
        {
            "table_id": int(table.id),
            "party_id": int(party.id) if party else None,
            "items": items,
            "count": len(items),
        },
        request=request,
    )

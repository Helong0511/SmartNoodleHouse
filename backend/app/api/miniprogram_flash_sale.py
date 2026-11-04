"""小程序顾客端：限量免费抢购（双端共用同一后端与数据库）。"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import CurrentCustomer, DbSession, OptionalCustomer
from app.core.response import success
from app.services.flash_sale_service import FlashSaleService

router = APIRouter(prefix="/miniprogram/flash-sales", tags=["小程序-抢购"])


class ClaimIn(BaseModel):
    store_id: int = Field(ge=1, description="核销门店，须参与该活动")
    client_request_id: str | None = Field(default=None, max_length=64)


@router.get("")
async def list_flash_sales(
    request: Request,
    db: DbSession,
    customer: OptionalCustomer,
    store_id: int = Query(..., ge=1, description="当前门店"),
):
    """本店可抢购的限量免费活动（含剩余名额与我的资格）。"""
    items = await FlashSaleService.list_active(
        db, store_id=int(store_id), customer_id=int(customer.id) if customer else None
    )
    return success({"items": items}, request=request)


@router.get("/mine")
async def list_my_claims(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    store_id: int | None = Query(default=None, ge=1),
):
    """我的抢购资格（待核销/已核销/已失效）。"""
    items = await FlashSaleService.list_my_claims(
        db, customer_id=int(customer.id), store_id=int(store_id) if store_id else None
    )
    return success({"items": items}, request=request)


@router.post("/{campaign_id}/claim")
async def claim(
    campaign_id: int,
    data: ClaimIn,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """抢购一份免费资格；库存条件 UPDATE 原子扣减，重复请求幂等返回。"""
    result = await FlashSaleService.claim(
        db,
        campaign_id=int(campaign_id),
        store_id=int(data.store_id),
        customer_id=int(customer.id),
        client_request_id=data.client_request_id,
    )
    await db.commit()
    return success(result, request=request)

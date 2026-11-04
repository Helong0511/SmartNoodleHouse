"""后台：限量免费抢购配置、参与门店与资格查询。

配置约束：
- FlashSaleConfig 与活动 1:1；总部活动（store_id 为空）默认全部门店参与，
  MarketingCampaignStore 仅记录例外门店（included=False）。
- 门店库存行可绑定本店餐品（FlashSaleStock.product_id）；products 按门店隔离，
  总部活动必须为每个门店指定本店餐品，不能回退到其他门店的商品。
- 库存调整不得低于已消耗资格数（CLAIMED/USED/EXPIRED 均占用名额）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import naive_local_now
from app.models import (
    Customer,
    FlashSaleClaim,
    FlashSaleConfig,
    FlashSaleStock,
    MarketingCampaign,
    MarketingCampaignStore,
    Product,
    Store,
    User,
)
from app.services.flash_sale_service import _CONSUMED_STATUSES
from app.services.marketing_service import MarketingService

_STATUS_LABEL = {
    "CLAIMED": "待核销",
    "USED": "已核销",
    "EXPIRED": "已失效",
    "CANCELLED": "已取消",
}


def _dt(value) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value else None


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


class FlashSaleAdminService:
    @staticmethod
    async def _campaign(db: AsyncSession, campaign_id: int) -> MarketingCampaign:
        camp = await db.get(MarketingCampaign, int(campaign_id))
        if not camp:
            raise AppException(40401, "活动不存在")
        return camp

    @staticmethod
    async def _product(db: AsyncSession, product_id: int, *, store_id: int, label: str) -> Product:
        product = await db.get(Product, int(product_id))
        if not product or int(product.store_id) != int(store_id):
            raise AppException(40014, f"{label}不属于该门店")
        if (product.status or "").upper() != "ON_SALE":
            raise AppException(40014, f"{label}未上架")
        return product

    @classmethod
    async def get_detail(cls, db: AsyncSession, campaign_id: int) -> dict:
        camp = await cls._campaign(db, campaign_id)
        cfg = await db.scalar(
            select(FlashSaleConfig).where(FlashSaleConfig.campaign_id == int(camp.id))
        )
        excluded_ids = set(
            (
                await db.execute(
                    select(MarketingCampaignStore.store_id).where(
                        MarketingCampaignStore.campaign_id == int(camp.id),
                        MarketingCampaignStore.included.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        stocks = {
            int(s.store_id): s
            for s in (
                await db.execute(
                    select(FlashSaleStock).where(FlashSaleStock.campaign_id == int(camp.id))
                )
            ).scalars().all()
        }
        # 门店活动只看本店；总部活动列出全部营业门店（含已配置库存的停用门店）
        if camp.store_id is not None:
            stores = [await db.get(Store, int(camp.store_id))]
        else:
            stores = list(
                (
                    await db.execute(select(Store).where(Store.status == 1).order_by(Store.id))
                ).scalars().all()
            )
            extra_ids = [sid for sid in stocks if sid not in {int(s.id) for s in stores if s}]
            for sid in sorted(extra_ids):
                s = await db.get(Store, int(sid))
                if s:
                    stores.append(s)

        product_ids: set[int] = set()
        if cfg and cfg.product_id:
            product_ids.add(int(cfg.product_id))
        for s in stocks.values():
            if s.product_id:
                product_ids.add(int(s.product_id))
        products = {
            int(p.id): p
            for p in (
                await db.execute(select(Product).where(Product.id.in_(product_ids)))
            ).scalars().all()
        } if product_ids else {}

        store_rows = []
        for store in stores:
            if store is None:
                continue
            sid = int(store.id)
            stock = stocks.get(sid)
            pid = int(stock.product_id) if stock and stock.product_id else None
            if pid is None and camp.store_id is not None and cfg and cfg.product_id:
                pid = int(cfg.product_id)
            product = products.get(pid) if pid else None
            store_rows.append(
                {
                    "store_id": sid,
                    "store_no": store.store_no,
                    "store_name": store.name,
                    "included": sid not in excluded_ids,
                    "product_id": pid,
                    "product_name": product.name if product else None,
                    "total_quantity": int(stock.total_quantity) if stock else 0,
                    "remaining_quantity": int(stock.remaining_quantity) if stock else 0,
                    "sold_quantity": (int(stock.total_quantity) - int(stock.remaining_quantity))
                    if stock
                    else 0,
                }
            )
        default_product = products.get(int(cfg.product_id)) if cfg and cfg.product_id else None
        return {
            "campaign": await MarketingService.payload_for(db, camp),
            "config": {
                "product_id": int(cfg.product_id) if cfg and cfg.product_id else None,
                "product_name": default_product.name if default_product else None,
                "per_customer_limit": max(1, int(cfg.per_customer_limit or 1)) if cfg else 1,
                "claim_valid_minutes": max(1, int(cfg.claim_valid_minutes or 30)) if cfg else 30,
                "verify_deadline_at": _dt(cfg.verify_deadline_at) if cfg else None,
                "notice": cfg.notice if cfg else None,
            }
            if cfg
            else None,
            "stores": store_rows,
            "excluded_store_ids": sorted(excluded_ids),
            "is_hq": camp.store_id is None,
        }

    @classmethod
    async def upsert_config(
        cls,
        db: AsyncSession,
        *,
        campaign_id: int,
        product_id: int,
        per_customer_limit: int,
        claim_valid_minutes: int,
        verify_deadline_at: datetime | None,
        notice: str | None,
        store_quotas: list[dict],
    ) -> dict:
        camp = await cls._campaign(db, campaign_id)
        if camp.store_id is not None:
            await cls._product(
                db, product_id, store_id=int(camp.store_id), label="抢购餐品"
            )
        else:
            product = await db.get(Product, int(product_id))
            if not product:
                raise AppException(40014, "抢购餐品不存在")

        cfg = await db.scalar(
            select(FlashSaleConfig)
            .where(FlashSaleConfig.campaign_id == int(camp.id))
            .with_for_update()
        )
        used = int(
            await db.scalar(
                select(func.count())
                .select_from(FlashSaleClaim)
                .where(
                    FlashSaleClaim.campaign_id == int(camp.id),
                    FlashSaleClaim.status.in_(_CONSUMED_STATUSES),
                )
            )
            or 0
        )
        if cfg and int(cfg.product_id) != int(product_id) and used > 0:
            raise AppException(40014, "已有顾客抢购，不能更换抢购餐品")
        if cfg is None:
            cfg = FlashSaleConfig(campaign_id=int(camp.id), product_id=int(product_id))
            db.add(cfg)
        cfg.product_id = int(product_id)
        cfg.per_customer_limit = max(1, int(per_customer_limit))
        cfg.claim_valid_minutes = max(1, int(claim_valid_minutes))
        cfg.verify_deadline_at = _naive(verify_deadline_at)
        cfg.notice = (notice or "").strip() or None
        await db.flush()

        for q in store_quotas:
            sid = int(q["store_id"])
            store = await db.get(Store, sid)
            if not store:
                raise AppException(40404, f"门店不存在: {sid}")
            if camp.store_id is not None and sid != int(camp.store_id):
                raise AppException(40014, "门店活动只能配置本店库存")
            stock = await db.scalar(
                select(FlashSaleStock)
                .where(
                    FlashSaleStock.campaign_id == int(camp.id),
                    FlashSaleStock.store_id == sid,
                )
                .with_for_update()
            )
            pid = q.get("product_id")
            if pid is None and stock and stock.product_id:
                pid = int(stock.product_id)
            if pid is None:
                p = await db.get(Product, int(cfg.product_id))
                if p and int(p.store_id) == sid:
                    pid = int(cfg.product_id)
            if pid is None:
                raise AppException(40014, f"请为门店「{store.name}」选择本店抢购餐品")
            await cls._product(db, int(pid), store_id=sid, label=f"门店「{store.name}」抢购餐品")

            consumed = int(
                await db.scalar(
                    select(func.count())
                    .select_from(FlashSaleClaim)
                    .where(
                        FlashSaleClaim.campaign_id == int(camp.id),
                        FlashSaleClaim.store_id == sid,
                        FlashSaleClaim.status.in_(_CONSUMED_STATUSES),
                    )
                )
                or 0
            )
            total = max(0, int(q["total_quantity"]))
            if total < consumed:
                raise AppException(
                    40014,
                    f"门店「{store.name}」总量不能低于已消耗名额（{consumed}）",
                )
            if stock is None:
                stock = FlashSaleStock(
                    campaign_id=int(camp.id),
                    store_id=sid,
                    product_id=int(pid),
                    total_quantity=total,
                    remaining_quantity=total - consumed,
                )
                db.add(stock)
            else:
                stock.product_id = int(pid)
                stock.total_quantity = total
                stock.remaining_quantity = total - consumed
        await db.flush()
        return await cls.get_detail(db, int(camp.id))

    @classmethod
    async def set_participation(
        cls, db: AsyncSession, *, campaign_id: int, excluded_store_ids: list[int]
    ) -> dict:
        camp = await cls._campaign(db, campaign_id)
        if camp.store_id is not None:
            raise AppException(40014, "门店活动无需配置参与范围")
        ids = sorted({int(x) for x in excluded_store_ids})
        if ids:
            found = set(
                (
                    await db.execute(select(Store.id).where(Store.id.in_(ids)))
                )
                .scalars()
                .all()
            )
            missing = [sid for sid in ids if sid not in found]
            if missing:
                raise AppException(40404, f"门店不存在: {missing[0]}")
        await db.execute(
            delete(MarketingCampaignStore).where(
                MarketingCampaignStore.campaign_id == int(camp.id)
            )
        )
        for sid in ids:
            db.add(
                MarketingCampaignStore(
                    campaign_id=int(camp.id), store_id=sid, included=False
                )
            )
        await db.flush()
        return {"campaign_id": int(camp.id), "excluded_store_ids": ids}

    @classmethod
    async def list_claims(
        cls,
        db: AsyncSession,
        *,
        store_id: int | None,
        campaign_id: int | None,
        status: str | None,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict], int]:
        base = select(FlashSaleClaim)
        if store_id is not None:
            base = base.where(FlashSaleClaim.store_id == int(store_id))
        if campaign_id:
            base = base.where(FlashSaleClaim.campaign_id == int(campaign_id))
        st = (status or "").strip().upper()
        if st:
            base = base.where(FlashSaleClaim.status == st)
        kw = (keyword or "").strip()
        if kw:
            like = f"%{kw}%"
            base = base.join(Customer, Customer.id == FlashSaleClaim.customer_id).where(
                or_(
                    FlashSaleClaim.claim_no.like(like),
                    Customer.customer_no.like(like),
                    Customer.display_name.like(like),
                    Customer.nickname.like(like),
                )
            )
        total = int(await db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        rows = list(
            (
                await db.execute(
                    base.order_by(FlashSaleClaim.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return [], total
        cust_ids = {int(r.customer_id) for r in rows}
        camp_ids = {int(r.campaign_id) for r in rows}
        store_ids = {int(r.store_id) for r in rows}
        user_ids = {int(r.verified_by) for r in rows if r.verified_by}
        customers = {
            int(c.id): c
            for c in (
                await db.execute(select(Customer).where(Customer.id.in_(cust_ids)))
            ).scalars().all()
        }
        campaigns = {
            int(c.id): c
            for c in (
                await db.execute(select(MarketingCampaign).where(MarketingCampaign.id.in_(camp_ids)))
            ).scalars().all()
        }
        stores = {
            int(s.id): s
            for s in (
                await db.execute(select(Store).where(Store.id.in_(store_ids)))
            ).scalars().all()
        }
        users = {
            int(u.id): u
            for u in (
                await db.execute(select(User).where(User.id.in_(user_ids)))
            ).scalars().all()
        } if user_ids else {}

        now = naive_local_now()
        out: list[dict] = []
        for r in rows:
            c = customers.get(int(r.customer_id))
            camp = campaigns.get(int(r.campaign_id))
            store = stores.get(int(r.store_id))
            verifier = users.get(int(r.verified_by)) if r.verified_by else None
            effective_status = r.status
            if r.status == "CLAIMED" and r.expires_at and r.expires_at <= now:
                effective_status = "EXPIRED"
            out.append(
                {
                    "id": int(r.id),
                    "claim_no": r.claim_no,
                    "status": effective_status,
                    "status_label": _STATUS_LABEL.get(effective_status, effective_status),
                    "campaign_id": int(r.campaign_id),
                    "campaign_name": camp.name if camp else None,
                    "store_id": int(r.store_id),
                    "store_name": store.name if store else None,
                    "customer_id": int(r.customer_id),
                    "customer_no": c.customer_no if c else None,
                    "customer_name": (
                        (c.display_name or "").strip() or (c.nickname or "").strip() if c else ""
                    )
                    or None,
                    "unit_no": int(r.unit_no),
                    "claimed_at": _dt(r.claimed_at),
                    "expires_at": _dt(r.expires_at),
                    "verify_deadline_at": _dt(r.verify_deadline_at),
                    "order_id": int(r.order_id) if r.order_id else None,
                    "verified_at": _dt(r.verified_at),
                    "verified_by": int(r.verified_by) if r.verified_by else None,
                    "verified_by_name": (
                        ((verifier.display_name or "").strip() or (verifier.username or "").strip())
                        if verifier
                        else None
                    ),
                }
            )
        return out, total

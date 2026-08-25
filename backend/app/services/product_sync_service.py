"""外卖菜单同步到美团/饿了么"""
from __future__ import annotations

import json
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DeliveryProductMap, PlatformStoreBinding, Product, ProductCategory
from app.services.delivery_context import get_credentials_for_store
from app.services.delivery_providers import get_delivery_provider

logger = logging.getLogger(__name__)


class ProductSyncService:
    @classmethod
    async def sync_store_menu(cls, db: AsyncSession, store_id: int, platform: str) -> dict:
        binding = (
            await db.execute(
                select(PlatformStoreBinding).where(
                    PlatformStoreBinding.store_id == store_id,
                    PlatformStoreBinding.platform == platform.upper(),
                    PlatformStoreBinding.status == 1,
                )
            )
        ).scalar_one_or_none()
        if not binding:
            return {"ok": False, "message": "未绑定平台门店，请先在后台配置"}

        products = (
            await db.execute(
                select(Product).where(Product.status == "ON_SALE").order_by(Product.sort_order)
            )
        ).scalars().all()

        provider = get_delivery_provider(platform, await get_credentials_for_store(db, store_id, platform))
        synced = 0
        errors: list[str] = []

        for p in products:
            try:
                payload = await cls._build_product_payload(db, p, binding.platform_shop_id)
                ok = await provider.sync_product(payload)
                if ok:
                    synced += 1
                    await cls._ensure_default_map(db, store_id, platform, p)
                else:
                    errors.append(p.name)
            except Exception as e:
                logger.exception("同步商品失败 %s: %s", p.name, e)
                errors.append(f"{p.name}:{e}")

        return {"ok": True, "synced": synced, "total": len(products), "errors": errors}

    @classmethod
    async def sync_prices(cls, db: AsyncSession, store_id: int, platform: str) -> dict:
        maps = (
            await db.execute(
                select(DeliveryProductMap).where(
                    DeliveryProductMap.store_id == store_id,
                    DeliveryProductMap.platform == platform.upper(),
                    DeliveryProductMap.status == 1,
                )
            )
        ).scalars().all()
        provider = get_delivery_provider(platform, await get_credentials_for_store(db, store_id, platform))
        items = []
        for m in maps:
            product = await db.get(Product, m.product_id)
            if not product:
                continue
            items.append(
                {
                    "app_food_code": m.app_food_code or product.product_no,
                    "platform_sku_id": m.platform_sku_id,
                    "price": str(product.price),
                }
            )
        count = await provider.sync_prices(items)
        return {"ok": True, "updated": count}

    @classmethod
    async def sync_stock(cls, db: AsyncSession, store_id: int, platform: str) -> dict:
        maps = (
            await db.execute(
                select(DeliveryProductMap).where(
                    DeliveryProductMap.store_id == store_id,
                    DeliveryProductMap.platform == platform.upper(),
                    DeliveryProductMap.status == 1,
                )
            )
        ).scalars().all()
        provider = get_delivery_provider(platform, await get_credentials_for_store(db, store_id, platform))
        items = []
        for m in maps:
            product = await db.get(Product, m.product_id)
            if not product:
                continue
            items.append(
                {
                    "app_food_code": m.app_food_code or product.product_no,
                    "platform_sku_id": m.platform_sku_id,
                    "stock": product.stock,
                }
            )
        count = await provider.sync_stock(items)
        return {"ok": True, "updated": count}

    @classmethod
    async def _build_product_payload(cls, db: AsyncSession, product: Product, shop_id: str) -> dict:
        cat = await db.get(ProductCategory, product.category_id)
        skus = [{"sku_id": product.product_no, "price": str(product.price), "spec": "默认"}]
        return {
            "shop_id": shop_id,
            "app_food_code": product.product_no,
            "name": product.name,
            "description": product.description or "",
            "category_name": cat.name if cat else "默认分类",
            "price": str(product.price),
            "box_price": str(product.box_price),
            "min_order_count": product.min_order_count,
            "stock": product.stock,
            "picture": product.main_image or "",
            "skus": skus,
        }

    @classmethod
    async def _ensure_default_map(cls, db: AsyncSession, store_id: int, platform: str, product: Product) -> None:
        sku = product.product_no
        existing = (
            await db.execute(
                select(DeliveryProductMap).where(
                    DeliveryProductMap.store_id == store_id,
                    DeliveryProductMap.platform == platform.upper(),
                    DeliveryProductMap.platform_sku_id == sku,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return
        db.add(
            DeliveryProductMap(
                store_id=store_id,
                platform=platform.upper(),
                platform_sku_id=sku,
                platform_spu_code=product.product_no,
                app_food_code=product.product_no,
                platform_sku_name=product.name,
                product_id=product.id,
            )
        )
        await db.flush()

    @staticmethod
    def build_option_signature(option_codes: list[str]) -> str:
        return "_".join(sorted(option_codes)) if option_codes else "DEFAULT"

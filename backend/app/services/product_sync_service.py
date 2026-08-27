"""外卖菜单同步到美团/饿了么（批量查询，平台失败不算本地成功）"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException
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

        products = list(
            (
                await db.execute(select(Product).where(Product.status == "ON_SALE").order_by(Product.sort_order))
            ).scalars().all()
        )
        cat_ids = list({p.category_id for p in products})
        cats = {
            c.id: c
            for c in (
                await db.execute(select(ProductCategory).where(ProductCategory.id.in_(cat_ids or [-1])))
            ).scalars().all()
        }

        creds = await get_credentials_for_store(db, platform, store_id)
        if not creds:
            raise AppException(40063, "缺少平台凭证")
        provider = get_delivery_provider(platform, creds)
        synced = 0
        errors: list[str] = []

        for p in products:
            try:
                payload = cls._build_product_payload_cached(p, cats.get(p.category_id), binding.platform_shop_id)
                ok = await provider.sync_product(payload)
                if ok is True:
                    synced += 1
                    await cls._ensure_default_map(db, store_id, platform, p)
                else:
                    errors.append(f"{p.name}:平台返回失败")
            except Exception as e:
                logger.exception("同步商品失败 %s: %s", p.name, e)
                errors.append(f"{p.name}:{e}")

        return {"ok": len(errors) == 0, "synced": synced, "total": len(products), "errors": errors}

    @classmethod
    async def sync_prices(cls, db: AsyncSession, store_id: int, platform: str) -> dict:
        maps, products = await cls._load_maps_products(db, store_id, platform)
        provider = get_delivery_provider(platform, await get_credentials_for_store(db, platform, store_id))
        items = [
            {
                "app_food_code": m.app_food_code or products[m.product_id].product_no,
                "platform_sku_id": m.platform_sku_id,
                "price": str(products[m.product_id].price),
            }
            for m in maps
            if m.product_id in products
        ]
        count = await provider.sync_prices(items)
        return {"ok": count == len(items), "updated": count, "total": len(items)}

    @classmethod
    async def sync_stock(cls, db: AsyncSession, store_id: int, platform: str) -> dict:
        maps, products = await cls._load_maps_products(db, store_id, platform)
        provider = get_delivery_provider(platform, await get_credentials_for_store(db, platform, store_id))
        items = [
            {
                "app_food_code": m.app_food_code or products[m.product_id].product_no,
                "platform_sku_id": m.platform_sku_id,
                "stock": 9999 if products[m.product_id].stock >= 999 else products[m.product_id].stock,
            }
            for m in maps
            if m.product_id in products
        ]
        # 修复：原先可能把本地 stock 原样推平台导致 999=不限被当成 999 份
        count = await provider.sync_stock(items)
        return {"ok": count == len(items), "updated": count, "total": len(items)}

    @classmethod
    async def _load_maps_products(cls, db: AsyncSession, store_id: int, platform: str):
        maps = list(
            (
                await db.execute(
                    select(DeliveryProductMap).where(
                        DeliveryProductMap.store_id == store_id,
                        DeliveryProductMap.platform == platform.upper(),
                        DeliveryProductMap.status == 1,
                    )
                )
            ).scalars().all()
        )
        pids = list({m.product_id for m in maps})
        products = {
            p.id: p
            for p in (
                await db.execute(select(Product).where(Product.id.in_(pids or [-1])))
            ).scalars().all()
        }
        return maps, products

    @classmethod
    def _build_product_payload_cached(cls, product: Product, cat: ProductCategory | None, shop_id: str) -> dict:
        skus = [{"sku_id": product.product_no, "price": str(product.price), "spec": "默认"}]
        stock = 9999 if product.stock >= 999 else product.stock
        return {
            "shop_id": shop_id,
            "app_food_code": product.product_no,
            "name": product.name,
            "description": product.description or "",
            "category_name": cat.name if cat else "默认分类",
            "price": str(product.price),
            "box_price": str(getattr(product, "box_price", 0) or 0),
            "min_order_count": getattr(product, "min_order_count", 1) or 1,
            "stock": stock,
            "picture": product.main_image or "",
            "skus": skus,
        }

    @classmethod
    async def _build_product_payload(cls, db: AsyncSession, product: Product, shop_id: str) -> dict:
        cat = await db.get(ProductCategory, product.category_id)
        return cls._build_product_payload_cached(product, cat, shop_id)

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

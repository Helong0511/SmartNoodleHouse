"""外卖菜单同步到美团/饿了么（批量查询，平台失败不算本地成功）"""
from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.models import (
    DeliveryProductMap,
    PlatformCategoryMap,
    PlatformStoreBinding,
    Product,
    ProductCategory,
)
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
                await db.execute(select(Product).where(Product.store_id == store_id, Product.status == "ON_SALE").order_by(Product.sort_order))
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
                result = await provider.sync_product(payload)
                if result.get("ok"):
                    synced += 1
                    # 保存平台返回的真实 ID（饿了么 itemId），美团用 app_food_code
                    platform_id = result.get("platform_id") or payload["app_food_code"]
                    await cls._ensure_default_map(db, store_id, platform, p, platform_id)
                else:
                    errors.append(f"{p.name}:{result.get('error') or '平台返回失败'}")
            except Exception as e:
                logger.exception("同步商品失败 %s: %s", p.name, e)
                errors.append(f"{p.name}:{e}")

        return {"ok": len(errors) == 0, "synced": synced, "total": len(products), "errors": errors}

    @classmethod
    async def sync_single_product(cls, db: AsyncSession, store_id: int, platform: str, product_id: int) -> dict:
        """推送单个商品到指定外卖平台。"""
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
            return {"ok": False, "message": "未绑定平台门店"}

        product = await db.get(Product, product_id)
        if not product:
            return {"ok": False, "message": "商品不存在"}

        cat = await db.get(ProductCategory, product.category_id) if product.category_id else None
        creds = await get_credentials_for_store(db, platform, store_id)
        if not creds:
            raise AppException(40063, "缺少平台凭证")
        provider = get_delivery_provider(platform, creds)

        payload = cls._build_product_payload_cached(product, cat, binding.platform_shop_id)
        result = await provider.sync_product(payload)
        if result.get("ok"):
            platform_id = result.get("platform_id") or payload["app_food_code"]
            await cls._ensure_default_map(db, store_id, platform, product, platform_id)
            return {"ok": True, "product": product.name, "platform": platform}
        return {"ok": False, "product": product.name, "error": result.get("error") or "平台返回失败"}

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
    async def _ensure_default_map(
        cls,
        db: AsyncSession,
        store_id: int,
        platform: str,
        product: Product,
        platform_sku_id: str | None = None,
    ) -> None:
        """同步后落库平台 SKU 映射。
        - 美团：platform_sku_id = app_food_code = 本地 product_no（商家自编码）
        - 饿了么：platform_sku_id = 平台返回的 itemId（必须保存，否则后续 updateItem 失败）
        """
        sku = platform_sku_id or product.product_no
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
            # 饿了么重新同步可能拿到新 itemId，更新映射
            existing.platform_sku_name = product.name
            existing.platform_spu_code = product.product_no
            existing.app_food_code = product.product_no
            existing.product_id = product.id
            existing.status = 1
            await db.flush()
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

    # ── 步骤5：批量上下架 / 库存对账 / 平台分类映射 ──

    @classmethod
    async def set_shelf(
        cls,
        db: AsyncSession,
        store_id: int,
        platform: str,
        on_shelf: bool,
        product_ids: list[int] | None = None,
    ) -> dict:
        """外卖平台侧批量上架/下架（不改变本地堂食上下架状态）。"""
        stmt = select(DeliveryProductMap).where(
            DeliveryProductMap.store_id == store_id,
            DeliveryProductMap.platform == platform.upper(),
            DeliveryProductMap.status == 1,
        )
        if product_ids:
            stmt = stmt.where(DeliveryProductMap.product_id.in_(list(product_ids)))
        maps = list((await db.execute(stmt)).scalars().all())
        if not maps:
            return {"ok": True, "updated": 0, "total": 0, "on_shelf": on_shelf}

        items = [
            {"app_food_code": m.app_food_code or m.platform_sku_id, "platform_sku_id": m.platform_sku_id}
            for m in maps
        ]
        provider = get_delivery_provider(platform, await get_credentials_for_store(db, platform, store_id))
        done = await provider.set_sell_status(items, on_shelf)
        return {"ok": done == len(items), "updated": done, "total": len(items), "on_shelf": on_shelf}

    @classmethod
    async def reconcile_stock(
        cls,
        db: AsyncSession,
        store_id: int | None = None,
        platform: str | None = None,
    ) -> dict:
        """定时/手动对账：以本地门店库存为准，覆盖推送平台（最终一致，幂等）。"""
        from app.services.delivery_sync_handlers import _push_store_product_stock

        stmt = (
            select(DeliveryProductMap.store_id, DeliveryProductMap.product_id)
            .where(DeliveryProductMap.status == 1)
            .distinct()
        )
        if store_id:
            stmt = stmt.where(DeliveryProductMap.store_id == store_id)
        if platform:
            stmt = stmt.where(DeliveryProductMap.platform == platform.upper())
        pairs = list((await db.execute(stmt)).all())

        failures = 0
        for sid, pid in pairs:
            failures += await _push_store_product_stock(db, int(sid), int(pid))
        return {"products": len(pairs), "failures": failures, "ok": failures == 0}

    @staticmethod
    async def list_category_maps(
        db: AsyncSession,
        store_id: int | None = None,
        platform: str | None = None,
    ) -> list[PlatformCategoryMap]:
        stmt = select(PlatformCategoryMap)
        if store_id is not None:
            stmt = stmt.where(
                (PlatformCategoryMap.store_id == store_id) | (PlatformCategoryMap.store_id.is_(None))
            )
        if platform:
            stmt = stmt.where(PlatformCategoryMap.platform == platform.upper())
        stmt = stmt.order_by(PlatformCategoryMap.store_id.is_(None), PlatformCategoryMap.platform)
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    async def upsert_category_map(
        db: AsyncSession,
        *,
        platform: str,
        local_category_id: int,
        platform_category_code: str | None = None,
        platform_category_name: str | None = None,
        store_id: int | None = None,
    ) -> PlatformCategoryMap:
        existing = (
            await db.execute(
                select(PlatformCategoryMap).where(
                    PlatformCategoryMap.platform == platform.upper(),
                    PlatformCategoryMap.local_category_id == local_category_id,
                    (
                        (PlatformCategoryMap.store_id == store_id)
                        if store_id is not None
                        else PlatformCategoryMap.store_id.is_(None)
                    ),
                )
            )
        ).scalar_one_or_none()
        if existing:
            existing.platform_category_code = platform_category_code
            existing.platform_category_name = platform_category_name
            existing.status = 1
            await db.flush()
            return existing
        row = PlatformCategoryMap(
            store_id=store_id,
            platform=platform.upper(),
            local_category_id=local_category_id,
            platform_category_code=platform_category_code,
            platform_category_name=platform_category_name,
            status=1,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def delete_category_map(db: AsyncSession, map_id: int) -> bool:
        result = await db.execute(
            delete(PlatformCategoryMap).where(PlatformCategoryMap.id == map_id)
        )
        return (result.rowcount or 0) > 0

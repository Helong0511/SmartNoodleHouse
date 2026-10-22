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
from app.services.scope_override import resolve_map_by_key

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
        cat_maps = await cls.resolve_category_maps(db, store_id=store_id, platform=platform)
        # 先组 payload 再释放连接占用；平台 HTTP 不拖着整菜单事务
        jobs = [
            (
                int(p.id),
                p.name,
                p.product_no,
                cls._build_product_payload_cached(
                    p,
                    cats.get(p.category_id),
                    binding.platform_shop_id,
                    cat_maps.get(int(p.category_id)) if p.category_id else None,
                ),
            )
            for p in products
        ]
        await db.commit()

        synced = 0
        errors: list[str] = []
        for product_id, name, product_no, payload in jobs:
            try:
                result = await provider.sync_product(payload)
                if result.get("ok"):
                    synced += 1
                    platform_id = result.get("platform_id") or payload["app_food_code"]
                    product = await db.get(Product, product_id)
                    if product is None:
                        # 会话已 commit，用轻量对象写映射
                        from types import SimpleNamespace

                        product = SimpleNamespace(
                            id=product_id, name=name, product_no=product_no
                        )
                    await cls._ensure_default_map(db, store_id, platform, product, platform_id)
                    await db.commit()
                else:
                    errors.append(f"{name}:{result.get('error') or '平台返回失败'}")
            except Exception as e:
                logger.exception("同步餐品失败 %s: %s", name, e)
                errors.append(f"{name}:{e}")
                try:
                    await db.rollback()
                except Exception:
                    pass

        return {"ok": len(errors) == 0, "synced": synced, "total": len(products), "errors": errors}

    @classmethod
    async def sync_single_product(cls, db: AsyncSession, store_id: int, platform: str, product_id: int) -> dict:
        """推送单个餐品到指定外卖平台。"""
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
            return {"ok": False, "message": "餐品不存在"}

        cat = await db.get(ProductCategory, product.category_id) if product.category_id else None
        creds = await get_credentials_for_store(db, platform, store_id)
        if not creds:
            raise AppException(40063, "缺少平台凭证")
        provider = get_delivery_provider(platform, creds)
        cat_map = None
        if product.category_id:
            cat_maps = await cls.resolve_category_maps(db, store_id=store_id, platform=platform)
            cat_map = cat_maps.get(int(product.category_id))

        payload = cls._build_product_payload_cached(product, cat, binding.platform_shop_id, cat_map)
        product_name = product.name
        await db.commit()
        result = await provider.sync_product(payload)
        if result.get("ok"):
            platform_id = result.get("platform_id") or payload["app_food_code"]
            product = await db.get(Product, product_id)
            if product is None:
                from types import SimpleNamespace

                product = SimpleNamespace(
                    id=product_id, name=product_name, product_no=payload["app_food_code"]
                )
            await cls._ensure_default_map(db, store_id, platform, product, platform_id)
            return {"ok": True, "product": product_name, "platform": platform}
        return {"ok": False, "product": product_name, "error": result.get("error") or "平台返回失败"}

    @classmethod
    async def sync_prices(cls, db: AsyncSession, store_id: int, platform: str) -> dict:
        maps, products = await cls._load_maps_products(db, store_id, platform)
        creds = await get_credentials_for_store(db, platform, store_id)
        items = [
            {
                "app_food_code": m.app_food_code or products[m.product_id].product_no,
                "platform_sku_id": m.platform_sku_id,
                "price": str(products[m.product_id].price),
            }
            for m in maps
            if m.product_id in products
        ]
        await db.commit()
        provider = get_delivery_provider(platform, creds)
        count = await provider.sync_prices(items)
        return {"ok": count == len(items), "updated": count, "total": len(items)}

    @classmethod
    async def sync_stock(cls, db: AsyncSession, store_id: int, platform: str) -> dict:
        maps, products = await cls._load_maps_products(db, store_id, platform)
        creds = await get_credentials_for_store(db, platform, store_id)
        items = [
            {
                "app_food_code": m.app_food_code or products[m.product_id].product_no,
                "platform_sku_id": m.platform_sku_id,
                "stock": 9999 if products[m.product_id].stock >= 999 else products[m.product_id].stock,
            }
            for m in maps
            if m.product_id in products
        ]
        await db.commit()
        provider = get_delivery_provider(platform, creds)
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
    def _build_product_payload_cached(
        cls,
        product: Product,
        cat: ProductCategory | None,
        shop_id: str,
        cat_map: PlatformCategoryMap | None = None,
    ) -> dict:
        skus = [{"sku_id": product.product_no, "price": str(product.price), "spec": "默认"}]
        stock = 9999 if product.stock >= 999 else product.stock
        # 分类名：门店覆盖总部映射优先，否则本地分类名
        category_name = (
            (cat_map.platform_category_name if cat_map and cat_map.platform_category_name else None)
            or (cat.name if cat else None)
            or "默认分类"
        )
        payload = {
            "shop_id": shop_id,
            "app_food_code": product.product_no,
            "name": product.name,
            "description": product.description or "",
            "category_name": category_name,
            "price": str(product.price),
            "box_price": str(getattr(product, "box_price", 0) or 0),
            "min_order_count": getattr(product, "min_order_count", 1) or 1,
            "stock": stock,
            "picture": product.main_image or "",
            "skus": skus,
        }
        if cat_map and cat_map.platform_category_code:
            payload["platform_category_code"] = cat_map.platform_category_code
        return payload

    @classmethod
    async def _build_product_payload(
        cls,
        db: AsyncSession,
        product: Product,
        shop_id: str,
        *,
        store_id: int | None = None,
        platform: str | None = None,
    ) -> dict:
        cat = await db.get(ProductCategory, product.category_id)
        cat_map = None
        if store_id and platform and product.category_id:
            resolved = await cls.resolve_category_maps(db, store_id=store_id, platform=platform)
            cat_map = resolved.get(int(product.category_id))
        return cls._build_product_payload_cached(product, cat, shop_id, cat_map)

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
        creds = await get_credentials_for_store(db, platform, store_id)
        await db.commit()
        provider = get_delivery_provider(platform, creds)
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
        """管理列表：返回 HQ∪店全量，不去重（便于分别维护）。"""
        stmt = select(PlatformCategoryMap)
        if store_id is not None:
            stmt = stmt.where(
                (PlatformCategoryMap.store_id == store_id) | (PlatformCategoryMap.store_id.is_(None))
            )
        if platform:
            stmt = stmt.where(PlatformCategoryMap.platform == platform.upper())
        stmt = stmt.order_by(PlatformCategoryMap.store_id.is_(None), PlatformCategoryMap.platform)
        return list((await db.execute(stmt)).scalars().all())

    @classmethod
    async def resolve_category_maps(
        cls,
        db: AsyncSession,
        *,
        store_id: int,
        platform: str,
    ) -> dict[int, PlatformCategoryMap]:
        """同步消费：按 local_category_id 解析，门店覆盖总部。"""
        rows = await cls.list_category_maps(db, store_id=store_id, platform=platform)
        active = [r for r in rows if int(getattr(r, "status", 1) or 0) == 1]
        resolved = resolve_map_by_key(active, key_fn=lambda r: int(r.local_category_id))
        return {int(k): v for k, v in resolved.items()}

    @classmethod
    async def resolve_category_map(
        cls,
        db: AsyncSession,
        *,
        store_id: int,
        platform: str,
        local_category_id: int,
    ) -> PlatformCategoryMap | None:
        """单键解析：有门店行用门店，否则总部。"""
        maps = await cls.resolve_category_maps(db, store_id=store_id, platform=platform)
        return maps.get(int(local_category_id))

    @staticmethod
    async def hq_category_map_override_warning(
        db: AsyncSession,
        *,
        store_id: int | None,
        platform: str,
        local_category_id: int,
    ) -> str | None:
        """门店分类映射与总部同键时提示同步将覆盖总部（不阻断）。"""
        if store_id is None:
            return None
        from app.services.scope_override import hq_override_warning

        hq = (
            await db.execute(
                select(PlatformCategoryMap)
                .where(
                    PlatformCategoryMap.store_id.is_(None),
                    PlatformCategoryMap.platform == platform.upper(),
                    PlatformCategoryMap.local_category_id == int(local_category_id),
                    PlatformCategoryMap.status == 1,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if not hq:
            return None
        label = hq.platform_category_name or hq.platform_category_code or str(hq.id)
        return hq_override_warning(
            key_label="外卖分类映射",
            key_desc=f"{platform.upper()} / 本地分类#{int(local_category_id)}",
            hq_name=str(label),
        )

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

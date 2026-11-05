"""外卖同步的 outbox handlers：库存售罄/恢复镜像等。

handler 一律以"重读本地最新状态再推送"为准，保证乱序、重复投递都收敛到最新值（幂等）。
在 outbox_worker.process_pending_outbox 中被 import 以完成注册。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select

from app.models import (
    DeliveryProductMap,
    PlatformStoreBinding,
    Product,
    StoreProductStock,
)
from app.services.delivery_context import get_credentials_for_store
from app.services.delivery_providers import get_delivery_provider
from app.services.outbox_worker import register_outbox_handler
from app.services.stock_service import UNLIMITED_STOCK

logger = logging.getLogger(__name__)

# 本地"不限库存"推给平台时使用的充足数值
_PLATFORM_UNLIMITED = 9999


async def _collect_stock_push_jobs(
    db, store_id: int, product_id: int, *, platform_filter: str | None = None
) -> list[tuple[str, Any, list[dict], bool]]:
    """读取本地库存与映射；可限定平台，以便每次推送前刷新快照。"""
    product = (
        await db.execute(
            select(Product).where(
                Product.id == product_id,
                Product.store_id == store_id,
            ).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if product is None:
        logger.info("库存镜像跳过：餐品不属于门店 store=%s product=%s", store_id, product_id)
        return []

    row = (
        await db.execute(
            select(StoreProductStock).where(
                StoreProductStock.store_id == store_id,
                StoreProductStock.product_id == product_id,
            ).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    # 商品主档停售具有最高优先级；门店库存覆盖不能把总部/主档已下架商品重新上架。
    if row is None:
        s = int(product.stock or 0)
        on_shelf = product.status == "ON_SALE"
    else:
        s = int(row.stock or 0)
        on_shelf = int(row.status) == 1 and product.status == "ON_SALE"
    target = (
        _PLATFORM_UNLIMITED
        if on_shelf and s >= UNLIMITED_STOCK
        else max(0, s) if on_shelf else 0
    )

    bindings = (
        await db.execute(
            select(PlatformStoreBinding).where(
                PlatformStoreBinding.store_id == store_id,
                PlatformStoreBinding.status == 1,
                *([PlatformStoreBinding.platform == platform_filter] if platform_filter else []),
            ).execution_options(populate_existing=True)
        )
    ).scalars().all()

    jobs: list[tuple[str, Any, list[dict], bool]] = []
    for binding in bindings:
        maps = (
            await db.execute(
                select(DeliveryProductMap).where(
                    DeliveryProductMap.store_id == store_id,
                    DeliveryProductMap.platform == binding.platform,
                    DeliveryProductMap.product_id == product_id,
                    DeliveryProductMap.status == 1,
                ).execution_options(populate_existing=True)
            )
        ).scalars().all()
        if not maps:
            continue  # 该平台尚未建档此餐品，无需推库存
        items = [
            {
                "app_food_code": m.app_food_code or m.platform_sku_id,
                "platform_sku_id": m.platform_sku_id,
                "stock": target,
            }
            for m in maps
        ]
        creds = await get_credentials_for_store(db, binding.platform, store_id)
        if creds is None:
            # 绑定存在但凭证读取失败/绑定被并发删除时，禁止使用 None 凭证继续请求。
            # Provider 可能仍持有全局 App 凭证，误把请求发往错误门店。
            raise RuntimeError(
                f"外卖平台门店凭证不可用，拒绝库存同步 store={store_id} "
                f"platform={binding.platform}"
            )
        jobs.append((binding.platform, creds, items, on_shelf))
    return jobs


async def _execute_stock_push_jobs(
    jobs: list[tuple[str, Any, list[dict], bool]],
    *,
    store_id: int,
    product_id: int,
) -> int:
    """纯平台 HTTP：不持有 DB 会话。返回失败平台数。"""
    failures = 0
    for platform, creds, items, on_shelf in jobs:
        try:
            provider = get_delivery_provider(platform, creds)
            stock_done = await provider.set_sku_stock(items)
            shelf_done = await provider.set_sell_status(items, on_shelf=on_shelf)
            if stock_done < len(items) or shelf_done < len(items):
                failures += 1
                logger.warning(
                    "库存/上下架镜像未全部成功 store=%s platform=%s product=%s stock=%s on_shelf=%s stock_done=%s shelf_done=%s",
                    store_id,
                    platform,
                    product_id,
                    items[0].get("stock") if items else None,
                    on_shelf,
                    stock_done,
                    shelf_done,
                )
            else:
                logger.info(
                    "库存镜像成功 store=%s platform=%s product=%s stock=%s on_shelf=%s",
                    store_id,
                    platform,
                    product_id,
                    items[0].get("stock") if items else None,
                    on_shelf,
                )
        except Exception:  # noqa: BLE001 单平台失败累计后由 outbox 重试（覆盖推送幂等）
            failures += 1
            logger.exception(
                "库存镜像异常 store=%s platform=%s product=%s",
                store_id,
                platform,
                product_id,
            )
    return failures


async def _push_store_product_stock(db, store_id: int, product_id: int) -> int:
    """把某门店某餐品的当前本地库存镜像到该店所有有效平台绑定。返回失败平台数。

    测试/对账可直接调用；outbox handler 会在 HTTP 前额外 commit 释放连接。
    """
    jobs = await _collect_stock_push_jobs(db, store_id, product_id)
    return await _execute_stock_push_jobs(jobs, store_id=store_id, product_id=product_id)


@register_outbox_handler("delivery_stock_base", "mirror")
@register_outbox_handler("delivery_stock", "mirror")
async def delivery_stock_mirror_handler(db, row) -> None:
    payload = json.loads(row.payload) if row.payload else {}
    store_id = payload.get("store_id")
    product_id = payload.get("product_id")
    if not store_id or not product_id:
        # 数据非法，重试无意义，直接成功丢弃
        logger.warning("库存镜像事件缺少 store_id/product_id id=%s payload=%s", row.id, payload)
        return
    sid, pid = int(store_id), int(product_id)
    # 先取有效平台列表。每个平台真正发送前重新读取库存、上下架、映射和凭证，
    # 读完立即 commit 释放连接，再执行 HTTP，避免后续平台沿用陈旧快照。
    platform_rows = await db.execute(
        select(PlatformStoreBinding.platform).where(
            PlatformStoreBinding.store_id == sid,
            PlatformStoreBinding.status == 1,
        )
    )
    platforms = list(dict.fromkeys(str(p) for p in platform_rows.scalars().all() if p))
    await db.commit()

    failures = 0
    for platform in platforms:
        db.expire_all()
        jobs = await _collect_stock_push_jobs(
            db, sid, pid, platform_filter=platform
        )
        await db.commit()
        failures += await _execute_stock_push_jobs(
            jobs, store_id=sid, product_id=pid
        )
    if failures:
        raise RuntimeError(f"库存镜像存在 {failures} 个平台失败，等待 outbox 重试")

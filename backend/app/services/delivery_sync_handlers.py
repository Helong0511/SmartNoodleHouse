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
    db, store_id: int, product_id: int
) -> list[tuple[str, Any, list[dict]]]:
    """只读本地库存与映射，拼出平台推送任务（不含 HTTP）。"""
    row = (
        await db.execute(
            select(StoreProductStock).where(
                StoreProductStock.store_id == store_id,
                StoreProductStock.product_id == product_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        logger.info("库存镜像跳过：门店库存行不存在 store=%s product=%s", store_id, product_id)
        return []

    if int(row.status) == 1:
        s = int(row.stock or 0)
        target = _PLATFORM_UNLIMITED if s >= UNLIMITED_STOCK else s
    else:
        target = 0  # 门店停售 = 平台售罄

    bindings = (
        await db.execute(
            select(PlatformStoreBinding).where(
                PlatformStoreBinding.store_id == store_id,
                PlatformStoreBinding.status == 1,
            )
        )
    ).scalars().all()

    jobs: list[tuple[str, Any, list[dict]]] = []
    for binding in bindings:
        maps = (
            await db.execute(
                select(DeliveryProductMap).where(
                    DeliveryProductMap.store_id == store_id,
                    DeliveryProductMap.platform == binding.platform,
                    DeliveryProductMap.product_id == product_id,
                    DeliveryProductMap.status == 1,
                )
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
        jobs.append((binding.platform, creds, items))
    return jobs


async def _execute_stock_push_jobs(
    jobs: list[tuple[str, Any, list[dict]]],
    *,
    store_id: int,
    product_id: int,
) -> int:
    """纯平台 HTTP：不持有 DB 会话。返回失败平台数。"""
    failures = 0
    for platform, creds, items in jobs:
        try:
            provider = get_delivery_provider(platform, creds)
            done = await provider.set_sku_stock(items)
            if done <= 0:
                failures += 1
                logger.warning(
                    "库存镜像平台返回0成功 store=%s platform=%s product=%s target=%s",
                    store_id,
                    platform,
                    product_id,
                    items[0].get("stock") if items else None,
                )
            else:
                logger.info(
                    "库存镜像成功 store=%s platform=%s product=%s stock=%s",
                    store_id,
                    platform,
                    product_id,
                    items[0].get("stock") if items else None,
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
    jobs = await _collect_stock_push_jobs(db, sid, pid)
    # claim 后的会话在此释放，平台 HTTP 不再占连接池
    await db.commit()
    failures = await _execute_stock_push_jobs(jobs, store_id=sid, product_id=pid)
    if failures:
        raise RuntimeError(f"库存镜像存在 {failures} 个平台失败，等待 outbox 重试")

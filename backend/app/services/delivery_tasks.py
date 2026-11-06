"""外卖平台异步回传任务（回调先应答，再调平台 API）"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import httpx

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.response import now_local
from app.models import DeliveryOrder, Order
from app.services.delivery_context import PlatformCredentials, get_credentials_for_store
from app.services.delivery_providers import DeliveryPlatformResultUnknown, get_delivery_provider
from app.services.print_tasks import handle_deferred_print
from app.services.outbox_worker import ExternalSideEffectUncertain, register_outbox_handler

logger = logging.getLogger(__name__)


async def _call_external_side_effect(
    action: str,
    platform: str,
    platform_order_id: str,
    operation: Callable[[], Awaitable[Any]],
) -> Any:
    """识别请求已可能送达下游但响应未知的传输故障，禁止 Outbox 自动重发。"""
    try:
        return await operation()
    except DeliveryPlatformResultUnknown as exc:
        raise ExternalSideEffectUncertain(
            f"{platform} {action} order={platform_order_id}: 平台响应格式/结构异常，无法确认副作用是否执行"
        ) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (408, 409) or exc.response.status_code >= 500:
            raise ExternalSideEffectUncertain(
                f"{platform} {action} order={platform_order_id}: HTTP {exc.response.status_code}; "
                "平台可能已执行，请核实平台订单状态后再处理"
            ) from exc
        raise
    except httpx.TransportError as exc:
        # 连接尚未建立/连接池未分配时可以安全重试；其他传输中断可能发生在请求发送之后。
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
            raise
        raise ExternalSideEffectUncertain(
            f"{platform} {action} order={platform_order_id}: {type(exc).__name__}; "
            "请求结果未知，请核实平台订单状态后再处理"
        ) from exc


async def run_deferred_tasks(tasks: list[dict[str, Any]]) -> None:
    if not tasks:
        return
    for task in tasks:
        action = task.get("action")
        if action == "print_ticket":
            try:
                await handle_deferred_print(task)
            except Exception as e:
                logger.exception("deferred print 失败 order_id=%s: %s", task.get("order_id"), e)
            continue
        platform = task.get("platform", "").upper()
        platform_order_id = task.get("platform_order_id", "")
        creds_data = task.get("credentials") or {}
        creds = PlatformCredentials(**creds_data) if creds_data else None
        provider = get_delivery_provider(platform, creds)
        try:
            if action == "confirm_order":
                ok = await _call_external_side_effect(
                    action, platform, platform_order_id,
                    lambda: provider.confirm_order(platform_order_id),
                )
                if ok is False and task.get("rollback_on_fail"):
                    # 只有平台明确返回拒绝才允许回滚；未知结果由下面的分支保留原状态。
                    await _rollback_confirm(task.get("order_id"), platform, platform_order_id)
            elif action == "reply_urge":
                await _call_external_side_effect(
                    action, platform, platform_order_id,
                    lambda: provider.reply_urge(platform_order_id, task.get("raw") or {}),
                )
            elif action == "cancel_order":
                reason = task.get("reason") or "商家取消"
                await _call_external_side_effect(
                    action, platform, platform_order_id,
                    lambda: provider.cancel_order(platform_order_id, reason),
                )
            else:
                logger.warning("未知 deferred task: %s", action)
        except ExternalSideEffectUncertain as e:
            # 旧调用路径没有持久化 UNCERTAIN 能力；宁可保留订单状态并报警，也绝不误回滚。
            logger.critical(
                "legacy deferred task external result uncertain; manual reconciliation required action=%s platform=%s order=%s error=%s",
                action, platform, platform_order_id, e,
            )
        except Exception as e:
            logger.exception("deferred task 失败 %s %s: %s", action, platform_order_id, e)
            if action == "confirm_order" and task.get("rollback_on_fail"):
                await _rollback_confirm(task.get("order_id"), platform, platform_order_id)


async def _rollback_confirm(order_id: int | None, platform: str, platform_order_id: str) -> None:
    async with AsyncSessionLocal() as db:
        d_order = None
        if order_id:
            d_order = (
                await db.execute(select(DeliveryOrder).where(DeliveryOrder.order_id == order_id))
            ).scalar_one_or_none()
        if not d_order:
            d_order = (
                await db.execute(
                    select(DeliveryOrder).where(
                        DeliveryOrder.platform == platform.upper(),
                        DeliveryOrder.platform_order_id == platform_order_id,
                    )
                )
            ).scalar_one_or_none()
        if not d_order:
            return
        d_order.delivery_status = "PENDING"
        d_order.last_sync_at = now_local()
        order = await db.get(Order, d_order.order_id)
        if order and order.status == "CONFIRMED":
            from app.services.order_lifecycle import apply_order_status

            await apply_order_status(
                db,
                order,
                "PAID",
                source="DELIVERY",
                assert_kind="SYSTEM",
                remark="平台接单失败回滚",
            )
        await db.commit()
        logger.error("平台接单失败，已回滚本地状态 order_id=%s platform=%s", d_order.order_id, platform)


@register_outbox_handler("delivery_manual", "confirm")
@register_outbox_handler("webhook_task", "execute")
async def execute_webhook_outbox_task(db, row) -> None:
    """执行已持久化的 Webhook 副作用任务。

    任务 payload 不保存平台密钥；每次执行均按 store_id 重新读取有效门店凭证。
    Outbox 采用至少一次投递，平台 API 必须可安全重试；本地状态由各业务服务幂等保护。
    """
    import json

    payload = json.loads(row.payload or "{}")
    if not isinstance(payload, dict):
        raise ValueError("webhook outbox payload must be an object")
    action = str(payload.get("action") or "").lower()
    if action == "print_ticket":
        order_id = int(payload.get("order_id") or 0)
        if not order_id:
            raise ValueError("print_ticket missing order_id")
        from app.services.print_service import PrintService, TICKET_CANCEL, TICKET_KITCHEN, TICKET_URGE

        ticket_type = str(payload.get("ticket_type") or TICKET_KITCHEN).upper()
        if ticket_type not in (TICKET_KITCHEN, TICKET_URGE, TICKET_CANCEL):
            raise ValueError("invalid print ticket type")
        await PrintService.print_order(db, order_id, ticket_type=ticket_type)
        return

    platform = str(payload.get("platform") or "").upper()
    platform_order_id = str(payload.get("platform_order_id") or "")
    store_id = int(payload.get("store_id") or 0)
    if platform not in {"MEITUAN", "ELEME"} or not platform_order_id or store_id <= 0:
        raise ValueError("webhook platform task missing platform/order/store")
    credentials = await get_credentials_for_store(db, platform, store_id)
    if credentials is None:
        raise RuntimeError(f"门店平台凭证不可用，拒绝执行任务 store={store_id} platform={platform}")
    # 先释放数据库事务/连接，再执行外部 HTTP。
    await db.commit()
    provider = get_delivery_provider(platform, credentials)
    if action == "confirm_order":
        ok = await _call_external_side_effect(
            action, platform, platform_order_id,
            lambda: provider.confirm_order(platform_order_id),
        )
        if ok is False and payload.get("rollback_on_fail"):
            await _rollback_confirm(payload.get("order_id"), platform, platform_order_id)
            return
        if ok is False:
            raise RuntimeError("平台接单确认失败")
    elif action == "cancel_order":
        ok = await _call_external_side_effect(
            action, platform, platform_order_id,
            lambda: provider.cancel_order(platform_order_id, str(payload.get("reason") or "商家取消")),
        )
        if ok is False:
            raise RuntimeError("平台取消订单失败")
        # 平台已明确确认取消后才更新本地订单；本地写入失败必须转 UNCERTAIN，
        # 不能让 worker 把同一平台取消动作当普通失败再次发送。
        try:
            order_id = int(payload.get("order_id") or 0)
            delivery_order_id = int(row.aggregate_id)
            order = await db.get(Order, order_id) if order_id else None
            d_order = await db.get(DeliveryOrder, delivery_order_id)
            if order is None or d_order is None:
                raise RuntimeError("平台取消已成功，但本地订单记录不存在")
            if order.status not in ("CANCELLED",):
                if order.status in ("COMPLETED", "REFUNDED"):
                    raise RuntimeError(f"平台取消已成功，但本地订单已处于终态 {order.status}")
                from app.services.order_lifecycle import apply_order_status

                await apply_order_status(
                    db,
                    order,
                    "CANCELLED",
                    source="DELIVERY",
                    assert_kind="SYSTEM",
                    remark=str(payload.get("reason") or "商家取消"),
                )
            d_order.delivery_status = "CANCELLED"
            d_order.last_sync_at = now_local()
            await db.flush()
        except Exception as exc:
            raise ExternalSideEffectUncertain(
                f"{platform} cancel_order order={platform_order_id}: 平台已确认取消，但本地状态持久化失败；"
                "禁止自动再次取消，请核对本地订单后处理"
            ) from exc
    elif action == "reply_urge":
        ok = await _call_external_side_effect(
            action, platform, platform_order_id,
            lambda: provider.reply_urge(platform_order_id, payload.get("raw") or {}),
        )
        if ok is False:
            raise RuntimeError("平台催单回复失败")
    else:
        raise ValueError(f"unsupported webhook outbox action: {action}")

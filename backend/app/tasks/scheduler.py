import logging
from datetime import timedelta

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.response import naive_local_now
from app.models import OperationLog, Order, VisitorEvent

logger = logging.getLogger(__name__)

from app.services.platform_token_service import PlatformTokenService
from app.services.webhook_inbox import process_pending_inbox


def register_tasks(scheduler) -> None:
    settings = get_settings()

    @scheduler.scheduled_job("interval", seconds=5)
    async def drain_webhook_inbox():
        n = await process_pending_inbox(limit=30)
        if n:
            logger.info("Processed %s webhook inbox items", n)

    @scheduler.scheduled_job("interval", hours=6)
    async def refresh_platform_tokens():
        count = await PlatformTokenService.refresh_expiring_tokens(within_hours=24)
        if count:
            logger.info("Refreshed %s platform tokens", count)

    @scheduler.scheduled_job("interval", minutes=1)
    async def close_expired_orders():
        async with AsyncSessionLocal() as db:
            from app.services.order_lifecycle import apply_order_status
            from app.services.payment_service import PaymentService
            from app.services.system_settings_service import SystemSettingsService
            from app.services.websocket_manager import ws_manager

            timeout_minutes = await SystemSettingsService.get_int(
                db, "order_payment_timeout_minutes", settings.order_payment_timeout_minutes
            )
            timeout = naive_local_now() - timedelta(minutes=timeout_minutes)
            # FOR UPDATE 与支付回调行锁互斥，避免关单/入账竞态
            result = await db.execute(
                select(Order)
                .where(
                    Order.status == "PENDING_PAYMENT",
                    Order.payment_status == "UNPAID",
                    Order.created_at <= timeout,
                )
                .with_for_update(skip_locked=True)
            )
            expired = list(result.scalars().all())
            closed = 0
            rescued = 0
            for order in expired:
                # 二次确认：锁内状态可能已被回调改写
                if order.status != "PENDING_PAYMENT" or order.payment_status != "UNPAID":
                    continue
                # 关单前先向渠道查单，避免回调丢失误关已付款订单
                try:
                    if await PaymentService.sync_before_close_order(db, order):
                        rescued += 1
                        try:
                            await ws_manager.notify_order_event(
                                "ORDER_PAID",
                                {
                                    "order_id": order.id,
                                    "order_no": order.order_no,
                                    "store_id": order.store_id,
                                    "source": "CLOSE_PRE_SYNC",
                                },
                            )
                        except Exception:
                            logger.exception("关单前入账 WS 通知失败 order_id=%s", order.id)
                        continue
                except Exception:
                    logger.exception("关单前查单异常 order_id=%s", order.id)
                if order.status != "PENDING_PAYMENT" or order.payment_status != "UNPAID":
                    continue
                await apply_order_status(
                    db,
                    order,
                    "CANCELLED",
                    source="TIMEOUT",
                    payment_status="CANCELLED",
                    assert_kind="SYSTEM",
                    remark="支付超时自动关单",
                )
                closed += 1
            await db.commit()
            if closed:
                logger.info("Expired orders closed: %s", closed)
            if rescued:
                logger.info("Expired orders rescued via payment query: %s", rescued)

    @scheduler.scheduled_job("interval", minutes=1)
    async def auto_complete_kitchen_orders():
        async with AsyncSessionLocal() as db:
            from app.services.kitchen_auto_service import auto_complete_stale_kitchen_orders
            from app.services.system_settings_service import SystemSettingsService
            from app.services.websocket_manager import ws_manager

            timeout_minutes = await SystemSettingsService.get_int(
                db, "kitchen_auto_complete_minutes", settings.kitchen_auto_complete_minutes
            )
            completed = await auto_complete_stale_kitchen_orders(db, timeout_minutes=timeout_minutes)
            await db.commit()
            for order in completed:
                try:
                    await ws_manager.notify_order_event(
                        "ORDER_COMPLETED",
                        {
                            "order_id": order.id,
                            "order_no": order.order_no,
                            "status": order.status,
                            "channel": order.channel,
                            "store_id": order.store_id,
                            "table_id": order.table_id,
                            "auto_kitchen": True,
                        },
                    )
                except Exception:
                    logger.exception("KDS auto-complete ws notify failed order_id=%s", order.id)
            if completed:
                logger.info("Kitchen auto-completed orders: %s", len(completed))

    @scheduler.scheduled_job("interval", minutes=1)
    async def poll_pending_payments():
        """补偿支付回调丢失：扫描 PENDING 微信/支付宝单并主动查单。"""
        from app.services.payment_service import PaymentService

        n = await PaymentService.poll_pending_payments(limit=30, min_age_seconds=30)
        if n:
            logger.info("Pending payments settled via query: %s", n)

    @scheduler.scheduled_job("interval", minutes=1)
    async def poll_payment_refunds():
        from app.services.payment_service import PaymentService

        n = await PaymentService.poll_pending_refunds(limit=20)
        if n:
            logger.info("Payment refunds confirmed via query: %s", n)

    @scheduler.scheduled_job("interval", hours=6)
    async def cleanup_visitor_data():
        async with AsyncSessionLocal() as db:
            await db.execute(delete(VisitorEvent).where(VisitorEvent.expires_at <= naive_local_now()))
            await db.commit()
            logger.info("Expired visitor data cleaned")

    @scheduler.scheduled_job("cron", hour=3, minute=0)
    async def cleanup_operation_logs():
        async with AsyncSessionLocal() as db:
            from app.services.system_settings_service import SystemSettingsService

            days = await SystemSettingsService.get_int(db, "operation_log_retention_days", 90)
            cutoff = naive_local_now() - timedelta(days=days)
            await db.execute(delete(OperationLog).where(OperationLog.created_at < cutoff))
            await db.commit()
            logger.info("Operation logs cleaned before %s", cutoff)

    @scheduler.scheduled_job("cron", hour=3, minute=30)
    async def maintain_file_logs():
        """文件日志：7 天明文→gzip，满月打包，超过 90 天删除。"""
        from app.services.file_log_service import maintain_logs

        stats = maintain_logs()
        if any(stats.values()):
            logger.info("File logs maintained: %s", stats)

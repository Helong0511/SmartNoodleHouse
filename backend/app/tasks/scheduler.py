import logging
from datetime import timedelta

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.response import now_local
from app.models import OperationLog, Order, VisitorEvent

logger = logging.getLogger(__name__)


from app.services.platform_token_service import PlatformTokenService


def register_tasks(scheduler) -> None:
    settings = get_settings()

    @scheduler.scheduled_job("interval", hours=6)
    async def refresh_platform_tokens():
        count = await PlatformTokenService.refresh_expiring_tokens(within_hours=24)
        if count:
            logger.info("Refreshed %s platform tokens", count)

    @scheduler.scheduled_job("interval", minutes=1)
    async def close_expired_orders():
        async with AsyncSessionLocal() as db:
            timeout = now_local() - timedelta(minutes=settings.order_payment_timeout_minutes)
            result = await db.execute(
                select(Order).where(
                    Order.status == "PENDING_PAYMENT",
                    Order.payment_status == "UNPAID",
                    Order.created_at <= timeout,
                )
            )
            for order in result.scalars():
                order.status = "CANCELLED"
                order.cancelled_at = now_local()
            await db.commit()
            logger.info("Expired orders closed")

    @scheduler.scheduled_job("interval", hours=6)
    async def cleanup_visitor_data():
        async with AsyncSessionLocal() as db:
            await db.execute(delete(VisitorEvent).where(VisitorEvent.expires_at <= now_local()))
            await db.commit()
            logger.info("Expired visitor data cleaned")

    @scheduler.scheduled_job("cron", hour=3, minute=0)
    async def cleanup_operation_logs():
        cutoff = now_local() - timedelta(days=settings.operation_log_retention_days)
        async with AsyncSessionLocal() as db:
            await db.execute(delete(OperationLog).where(OperationLog.created_at < cutoff))
            await db.commit()
            logger.info("Operation logs older than %s days cleaned", settings.operation_log_retention_days)

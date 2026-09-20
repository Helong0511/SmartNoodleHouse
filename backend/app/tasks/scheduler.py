import logging
from datetime import timedelta

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.response import naive_local_now
from app.models import OperationLog, Order, VisitorEvent

logger = logging.getLogger(__name__)

from app.services.platform_token_service import PlatformTokenService
from app.services.webhook_inbox import process_pending_inbox, reclaim_stale_inbox
from app.services.print_service import PrintService
from app.services.outbox_worker import process_pending_outbox, reclaim_stale_processing
from app.services.pii_cleanup_service import anonymize_expired_delivery_pii


def register_tasks(scheduler) -> None:
    settings = get_settings()

    def _leader_job(fn):
        """调度任务 fence：锁丢失后跳过，避免双 Leader 窗口继续跑任务。"""

        async def _wrapped(*args, **kwargs):
            from app.core.leader_lock import is_leader_held

            if not is_leader_held():
                return None
            return await fn(*args, **kwargs)

        _wrapped.__name__ = getattr(fn, "__name__", "leader_job")
        return _wrapped

    @scheduler.scheduled_job("interval", seconds=5)
    @_leader_job
    async def drain_webhook_inbox():
        n = await process_pending_inbox(limit=30)
        if n:
            logger.info("Processed %s webhook inbox items", n)

    @scheduler.scheduled_job("interval", seconds=10)
    @_leader_job
    async def retry_failed_print_jobs():
        n = await PrintService.process_pending_print_jobs(limit=20)
        if n:
            logger.info("Retried %s print jobs", n)

    @scheduler.scheduled_job("interval", hours=6)
    @_leader_job
    async def refresh_platform_tokens():
        count = await PlatformTokenService.refresh_expiring_tokens(within_hours=24)
        if count:
            logger.info("Refreshed %s platform tokens", count)

    @scheduler.scheduled_job("interval", minutes=1)
    @_leader_job
    async def auto_open_close_delivery_stores():
        # 外卖门店按本地营业时间自动开/关店（仅 auto_sync_open=true 的绑定）
        async with AsyncSessionLocal() as db:
            from app.services.store_hours_service import StoreHoursService

            stats = await StoreHoursService.apply_auto_open_tick(db)
            if stats["opened"] or stats["closed"]:
                logger.info("外卖自动开关店: %s", stats)

    @scheduler.scheduled_job("interval", minutes=5)
    @_leader_job
    async def delivery_stock_reconcile():
        # 外卖库存定时对账：以本地门店库存覆盖推送各平台，纠正任何漂移（最终一致）
        async with AsyncSessionLocal() as db:
            from app.services.product_sync_service import ProductSyncService

            res = await ProductSyncService.reconcile_stock(db)
            if res.get("failures"):
                logger.warning("外卖库存对账存在失败: %s", res)

    @scheduler.scheduled_job("interval", minutes=1)
    @_leader_job
    async def close_expired_orders():
        async with AsyncSessionLocal() as db:
            from app.services.order_lifecycle import apply_order_status
            from app.services.payment_service import PaymentService
            from app.services.system_settings_service import SystemSettingsService
            from app.services.websocket_manager import ws_manager

            global_timeout = await SystemSettingsService.effective_int(
                db, "order_payment_timeout_minutes", settings.order_payment_timeout_minutes
            )
            overrides_raw = await SystemSettingsService.store_override_map(
                db, "order_payment_timeout_minutes"
            )
            store_timeouts: dict[int, int] = {}
            for sid, val in overrides_raw.items():
                try:
                    store_timeouts[int(sid)] = int(val)
                except (TypeError, ValueError):
                    continue
            positive = [t for t in [global_timeout, *store_timeouts.values()] if t > 0]
            if not positive:
                return
            min_timeout = min(positive)
            timeout = naive_local_now() - timedelta(minutes=min_timeout)
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
            now = naive_local_now()
            for order in expired:
                # 二次确认：锁内状态可能已被回调改写
                if order.status != "PENDING_PAYMENT" or order.payment_status != "UNPAID":
                    continue
                minutes = store_timeouts.get(int(order.store_id), global_timeout)
                if minutes <= 0:
                    continue
                if order.created_at and order.created_at > now - timedelta(minutes=minutes):
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
                # P1 修复：关单前同步撤销渠道交易，避免渠道侧交易悬挂；
                # 若发现迟到成功支付（已自动入账），保留订单不关单。
                try:
                    if await PaymentService.close_channel_payments(db, order):
                        rescued += 1
                        try:
                            await ws_manager.notify_order_event(
                                "ORDER_PAID",
                                {
                                    "order_id": order.id,
                                    "order_no": order.order_no,
                                    "store_id": order.store_id,
                                    "source": "CLOSE_CHANNEL_LATE_PAID",
                                },
                            )
                        except Exception:
                            logger.exception("关单迟到入账 WS 通知失败 order_id=%s", order.id)
                        continue
                except Exception:
                    logger.exception("关单撤销渠道交易异常 order_id=%s", order.id)
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
    @_leader_job
    async def auto_complete_kitchen_orders():
        async with AsyncSessionLocal() as db:
            from app.services.kitchen_auto_service import auto_complete_stale_kitchen_orders
            from app.services.websocket_manager import ws_manager

            completed = await auto_complete_stale_kitchen_orders(db)
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
    @_leader_job
    async def poll_pending_payments():
        """补偿支付回调丢失：扫描 PENDING 微信/支付宝单并主动查单。"""
        from app.services.payment_service import PaymentService

        n = await PaymentService.poll_pending_payments(limit=30, min_age_seconds=30)
        if n:
            logger.info("Pending payments settled via query: %s", n)

    @scheduler.scheduled_job("interval", minutes=1)
    @_leader_job
    async def poll_payment_refunds():
        from app.services.payment_service import PaymentService

        n = await PaymentService.poll_pending_refunds(limit=20)
        if n:
            logger.info("Payment refunds confirmed via query: %s", n)

    @scheduler.scheduled_job("interval", hours=6)
    @_leader_job
    async def cleanup_visitor_data():
        async with AsyncSessionLocal() as db:
            await db.execute(delete(VisitorEvent).where(VisitorEvent.expires_at <= naive_local_now()))
            await db.commit()
            logger.info("Expired visitor data cleaned")

    @scheduler.scheduled_job("cron", hour=3, minute=0)
    @_leader_job
    async def cleanup_operation_logs():
        async with AsyncSessionLocal() as db:
            from app.services.system_settings_service import SystemSettingsService

            days = await SystemSettingsService.get_int(db, "operation_log_retention_days", 90)
            cutoff = naive_local_now() - timedelta(days=days)
            await db.execute(delete(OperationLog).where(OperationLog.created_at < cutoff))
            await db.commit()
            logger.info("Operation logs cleaned before %s", cutoff)

    @scheduler.scheduled_job("cron", hour=4, minute=0)
    @_leader_job
    async def cleanup_delivery_pii():
        """P1: 外卖订单 PII 脱敏 — 已完结超 30 天的 recipient/rider 字段置 NULL。"""
        async with AsyncSessionLocal() as db:
            n = await anonymize_expired_delivery_pii(db)
        if n:
            logger.info("Delivery PII anonymized: %s orders", n)

    @scheduler.scheduled_job("cron", hour=3, minute=30)
    @_leader_job
    async def maintain_file_logs():
        """文件日志：7 天明文→gzip，满月打包，超过 90 天删除。"""
        from app.services.file_log_service import maintain_logs

        stats = maintain_logs()
        if any(stats.values()):
            logger.info("File logs maintained: %s", stats)

    @scheduler.scheduled_job("interval", seconds=30)
    @_leader_job
    async def drain_outbox_events():
        """DB outbox worker：消费支付副作用/退款补偿等事务型事件。"""
        n = await process_pending_outbox(limit=30)
        if n:
            logger.info("Outbox events processed: %s", n)

    @scheduler.scheduled_job("interval", minutes=5)
    @_leader_job
    async def reclaim_stale_outbox():
        """回收崩溃残留的 PROCESSING outbox 行。"""
        async with AsyncSessionLocal() as db:
            n = await reclaim_stale_processing(db)
        if n:
            logger.info("Stale outbox PROCESSING reclaimed: %s", n)

    @scheduler.scheduled_job("interval", minutes=5)
    @_leader_job
    async def reclaim_stale_webhook_inbox():
        """回收崩溃残留的 PROCESSING webhook inbox 行。"""
        async with AsyncSessionLocal() as db:
            n = await reclaim_stale_inbox(db)
        if n:
            logger.info("Stale webhook inbox PROCESSING reclaimed: %s", n)

    @scheduler.scheduled_job("cron", minute="*/2")
    @_leader_job
    async def reclaim_stale_printing_jobs():
        """回收崩溃残留的 PRINTING 打印任务（locked_at > 5min 视为残留）。"""
        async with AsyncSessionLocal() as db:
            n = await PrintService.reclaim_stale_printing(db)
        if n:
            logger.info("Stale PRINTING print jobs reclaimed: %s", n)

    @scheduler.scheduled_job("interval", minutes=4)
    @_leader_job
    async def learn_taste_profiles():
        """智能推荐：增量消费最近 15 分钟行为事件，保证画像 5 分钟内更新（支付链路另有即时学习）。"""
        async with AsyncSessionLocal() as db:
            from app.services.taste_learning_service import TasteLearningService

            since = naive_local_now() - timedelta(minutes=15)
            try:
                stats = await TasteLearningService.learn_recent_customers(db, since=since, limit=200)
                await db.commit()
                if stats["events"]:
                    logger.info("口味画像兜底学习: %s", stats)
            except Exception:
                logger.exception("口味画像兜底学习任务异常")

    @scheduler.scheduled_job("interval", minutes=30)
    @_leader_job
    async def reencrypt_face_profiles():
        """密钥轮换后：把旧 version 人脸向量重加密到当前密钥。"""
        from app.services.face_service import FaceService

        async with AsyncSessionLocal() as db:
            n = await FaceService.reencrypt_stale_profiles(db, limit=50)
            if n:
                await db.commit()
                logger.info("Face profiles re-encrypted: %s", n)

    @scheduler.scheduled_job("cron", hour=3, minute=15)
    @_leader_job
    async def cleanup_expired_behavior_events():
        """隐私合规：清理超期顾客行为事件（默认 90 天，system_settings 可调）。"""
        from app.services.privacy_service import PrivacyService
        from app.services.system_settings_service import SystemSettingsService

        async with AsyncSessionLocal() as db:
            days = await SystemSettingsService.get_int(
                db, "behavior_event_retention_days", 90
            )
            n = await PrivacyService.cleanup_expired_events(db, retention_days=days)
        if n:
            logger.info("Expired behavior events cleaned: %s rows (retention=%sd)", n, days)

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

# 定时任务目录（管理后台展示真源；与 register_tasks 中 id 一一对应）
TASK_CATALOG: list[dict] = [
    {
        "id": "drain_webhook_inbox",
        "title": "处理 Webhook 收件箱",
        "category": "回调",
        "description": "消费支付/外卖等渠道回调 inbox，幂等入账。",
        "default_schedule": {"type": "interval", "unit": "seconds", "every": 5},
    },
    {
        "id": "retry_failed_print_jobs",
        "title": "重试待打印任务",
        "category": "打印",
        "description": "处理 PENDING 打印任务，失败自动重试。",
        "default_schedule": {"type": "interval", "unit": "seconds", "every": 10},
    },
    {
        "id": "refresh_platform_tokens",
        "title": "刷新平台 Token",
        "category": "外卖",
        "description": "刷新即将过期的外卖/平台 access_token。",
        "default_schedule": {"type": "interval", "unit": "hours", "every": 6},
    },
    {
        "id": "auto_open_close_delivery_stores",
        "title": "外卖门店自动开关店",
        "category": "外卖",
        "description": "按本地营业时间自动开/关店（仅 auto_sync_open 绑定）。",
        "default_schedule": {"type": "interval", "unit": "minutes", "every": 1},
    },
    {
        "id": "delivery_stock_reconcile",
        "title": "外卖库存对账",
        "category": "外卖",
        "description": "以本地门店库存覆盖推送各平台，纠正漂移。",
        "default_schedule": {"type": "interval", "unit": "minutes", "every": 5},
    },
    {
        "id": "close_expired_orders",
        "title": "支付超时关单",
        "category": "订单",
        "description": "关闭超时未支付订单；关单前先查单，避免误关已付款。",
        "default_schedule": {"type": "interval", "unit": "minutes", "every": 1},
    },
    {
        "id": "auto_complete_kitchen_orders",
        "title": "厨房超时自动完成",
        "category": "订单",
        "description": "KDS 滞留过久的制作中订单自动完成。",
        "default_schedule": {"type": "interval", "unit": "minutes", "every": 1},
    },
    {
        "id": "poll_pending_payments",
        "title": "支付查单补偿",
        "category": "支付",
        "description": "扫描 PENDING 微信/支付宝支付单并主动查单入账。",
        "default_schedule": {"type": "interval", "unit": "minutes", "every": 1},
    },
    {
        "id": "poll_payment_refunds",
        "title": "退款查单补偿",
        "category": "支付",
        "description": "拉取 PROCESSING/PENDING/UNKNOWN 退款单状态。",
        "default_schedule": {"type": "interval", "unit": "minutes", "every": 1},
    },
    {
        "id": "cleanup_visitor_data",
        "title": "清理访客临时数据",
        "category": "清理",
        "description": "删除已过期的 VisitorEvent。",
        "default_schedule": {"type": "interval", "unit": "hours", "every": 72},
    },
    {
        "id": "cleanup_operation_logs",
        "title": "清理操作日志",
        "category": "清理",
        "description": "按 operation_log_retention_days（默认 90 天）清理审计日志。",
        "default_schedule": {"type": "cron", "hour": 3, "minute": 0},
    },
    {
        "id": "cleanup_delivery_pii",
        "title": "外卖 PII 脱敏",
        "category": "隐私",
        "description": "已完结超 30 天的外卖收件人/骑手字段置空。",
        "default_schedule": {"type": "cron", "hour": 4, "minute": 0},
    },
    {
        "id": "maintain_file_logs",
        "title": "维护文件日志",
        "category": "清理",
        "description": "日志 gzip 归档与超期删除（约 90 天）。",
        "default_schedule": {"type": "cron", "hour": 3, "minute": 30},
    },
    {
        "id": "drain_outbox_events",
        "title": "消费 Outbox 事件",
        "category": "可靠投递",
        "description": "处理支付副作用、退款补偿等事务型 outbox。",
        "default_schedule": {"type": "interval", "unit": "seconds", "every": 30},
    },
    {
        "id": "reclaim_stale_outbox",
        "title": "回收卡住的 Outbox",
        "category": "可靠投递",
        "description": "回收崩溃残留的 PROCESSING outbox 行。",
        "default_schedule": {"type": "interval", "unit": "minutes", "every": 5},
    },
    {
        "id": "reclaim_stale_webhook_inbox",
        "title": "回收卡住的 Webhook Inbox",
        "category": "回调",
        "description": "回收崩溃残留的 PROCESSING webhook inbox 行。",
        "default_schedule": {"type": "interval", "unit": "minutes", "every": 5},
    },
    {
        "id": "reclaim_stale_printing_jobs",
        "title": "回收卡住的打印任务",
        "category": "打印",
        "description": "回收 locked_at 超时的 PRINTING 打印任务（复位为 RETRYING）。",
        "default_schedule": {"type": "cron_interval", "every_minutes": 2},
    },
    {
        "id": "learn_taste_profiles",
        "title": "口味画像增量学习",
        "category": "推荐",
        "description": "消费近 15 分钟行为事件，兜底更新顾客口味画像。",
        "default_schedule": {"type": "interval", "unit": "minutes", "every": 4},
    },
    {
        "id": "reencrypt_face_profiles",
        "title": "人脸向量密钥轮换",
        "category": "迎宾",
        "description": "把旧密钥版本的人脸向量重加密到当前密钥。",
        "default_schedule": {"type": "interval", "unit": "minutes", "every": 30},
    },
    {
        "id": "cleanup_expired_behavior_events",
        "title": "清理超期行为事件",
        "category": "隐私",
        "description": "按 behavior_event_retention_days（默认 90 天）清理行为事件。",
        "default_schedule": {"type": "cron", "hour": 3, "minute": 15},
    },
    {
        "id": "night_data_batch",
        "title": "关店夜间数据批处理",
        "category": "推荐",
        "description": "北京时间默认 02:10 启动：按销量刷新热销/推荐、整店重算商品口味画像、深度学习顾客智能口味。",
        "default_schedule": {"type": "cron", "hour": 2, "minute": 10},
    },
]


async def describe_scheduler_status(scheduler=None, db=None) -> dict:
    """供管理后台：目录 + 覆盖启停/周期 + 运行时 next_run。"""
    from app.core.config import get_settings
    from app.core.leader_lock import is_leader_held
    from app.services.scheduler_admin import (
        effective_schedule,
        format_trigger_label,
        load_overrides,
    )

    settings = get_settings()
    overrides: dict = {}
    if db is not None:
        overrides = await load_overrides(db)

    runtime: dict[str, dict] = {}
    scheduler_running = False
    if scheduler is not None:
        try:
            scheduler_running = bool(getattr(scheduler, "running", False))
            for job in scheduler.get_jobs():
                nxt = getattr(job, "next_run_time", None)
                runtime[str(job.id)] = {
                    "next_run_at": nxt.isoformat(sep=" ", timespec="seconds") if nxt else None,
                    "pending": bool(getattr(job, "pending", False)),
                    "paused": bool(getattr(job, "next_run_time", True) is None and scheduler_running),
                }
        except Exception:
            logger.exception("读取 scheduler jobs 失败")

    items = []
    for meta in TASK_CATALOG:
        jid = meta["id"]
        ov = overrides.get(jid) if isinstance(overrides.get(jid), dict) else {}
        schedule = effective_schedule(meta, ov)
        enabled = bool(ov.get("enabled", True)) if ov else True
        rt = runtime.get(jid, {})
        paused = False
        if jid in runtime:
            # APScheduler：pause 后 next_run_time 为 None
            paused = scheduler_running and rt.get("next_run_at") is None and not enabled
            if scheduler_running and not enabled:
                paused = True
        items.append(
            {
                "id": jid,
                "title": meta["title"],
                "category": meta["category"],
                "description": meta["description"],
                "default_schedule": meta.get("default_schedule"),
                "schedule": schedule,
                "trigger": format_trigger_label(schedule),
                "default_trigger": format_trigger_label(meta.get("default_schedule")),
                "enabled": enabled,
                "overridden": bool(ov),
                "next_run_at": rt.get("next_run_at"),
                "pending": rt.get("pending"),
                "registered": jid in runtime,
                "paused": paused or (jid in runtime and not enabled),
            }
        )
    return {
        "process_role": settings.process_role,
        "leader_held": bool(is_leader_held()),
        "scheduler_running": scheduler_running,
        "note": "可启停并修改周期；任意进程写入后经 Redis 通知，仅 Leader 热生效；非 Leader 进程 standby 争抢可在宕机后自动接管。",
        "total": len(items),
        "items": items,
    }


def register_tasks(scheduler) -> None:
    settings = get_settings()

    def _leader_job(fn):
        """调度任务 fence：内存旗标 + Redis 持锁校验，缩小双 Leader 窗口。"""

        async def _wrapped(*args, **kwargs):
            from app.core.leader_lock import confirm_leader_owned, is_leader_held
            from app.services.scheduler_admin import is_job_enabled

            if not is_leader_held():
                return None
            # 二次确认 Redis 锁值仍属本进程（TTL 过期换主后，续期循环最多延迟 RENEW_INTERVAL）
            if not await confirm_leader_owned():
                return None
            job_name = getattr(fn, "__name__", "")
            if job_name and not is_job_enabled(job_name):
                return None
            return await fn(*args, **kwargs)

        _wrapped.__name__ = getattr(fn, "__name__", "leader_job")
        return _wrapped

    @scheduler.scheduled_job(
        "interval", seconds=5, id="drain_webhook_inbox", name="处理 Webhook 收件箱"
    )
    @_leader_job
    async def drain_webhook_inbox():
        n = await process_pending_inbox(limit=30)
        if n:
            logger.info("Processed %s webhook inbox items", n)

    @scheduler.scheduled_job(
        "interval", seconds=10, id="retry_failed_print_jobs", name="重试待打印任务"
    )
    @_leader_job
    async def retry_failed_print_jobs():
        n = await PrintService.process_pending_print_jobs(limit=20)
        if n:
            logger.info("Retried %s print jobs", n)

    @scheduler.scheduled_job(
        "interval", hours=6, id="refresh_platform_tokens", name="刷新平台 Token"
    )
    @_leader_job
    async def refresh_platform_tokens():
        from app.services.scheduler_admin import redis_job_lock

        async with redis_job_lock("refresh_platform_tokens", ttl=600) as ok:
            if not ok:
                return
            count = await PlatformTokenService.refresh_expiring_tokens(within_hours=24)
            if count:
                logger.info("Refreshed %s platform tokens", count)

    @scheduler.scheduled_job(
        "interval",
        minutes=1,
        id="auto_open_close_delivery_stores",
        name="外卖门店自动开关店",
    )
    @_leader_job
    async def auto_open_close_delivery_stores():
        # 外卖门店按本地营业时间自动开/关店（仅 auto_sync_open=true 的绑定）
        from app.services.scheduler_admin import redis_job_lock

        async with redis_job_lock("auto_open_close_delivery_stores", ttl=90) as ok:
            if not ok:
                return
            async with AsyncSessionLocal() as db:
                from app.services.store_hours_service import StoreHoursService

                stats = await StoreHoursService.apply_auto_open_tick(db)
                if stats["opened"] or stats["closed"]:
                    logger.info("外卖自动开关店: %s", stats)

    @scheduler.scheduled_job(
        "interval", minutes=5, id="delivery_stock_reconcile", name="外卖库存对账"
    )
    @_leader_job
    async def delivery_stock_reconcile():
        # 外卖库存定时对账：以本地门店库存覆盖推送各平台，纠正任何漂移（最终一致）
        from app.services.scheduler_admin import redis_job_lock

        async with redis_job_lock("delivery_stock_reconcile", ttl=240) as ok:
            if not ok:
                return
            async with AsyncSessionLocal() as db:
                from app.services.product_sync_service import ProductSyncService

                res = await ProductSyncService.reconcile_stock(db)
                if res.get("failures"):
                    logger.warning("外卖库存对账存在失败: %s", res)

    @scheduler.scheduled_job(
        "interval", minutes=1, id="close_expired_orders", name="支付超时关单"
    )
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

    @scheduler.scheduled_job(
        "interval", minutes=1, id="auto_complete_kitchen_orders", name="厨房超时自动完成"
    )
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

    @scheduler.scheduled_job(
        "interval", minutes=1, id="poll_pending_payments", name="支付查单补偿"
    )
    @_leader_job
    async def poll_pending_payments():
        """补偿支付回调丢失：扫描 PENDING 微信/支付宝单并主动查单。"""
        from app.services.payment_service import PaymentService

        n = await PaymentService.poll_pending_payments(limit=30, min_age_seconds=30)
        if n:
            logger.info("Pending payments settled via query: %s", n)

    @scheduler.scheduled_job(
        "interval", minutes=1, id="poll_payment_refunds", name="退款查单补偿"
    )
    @_leader_job
    async def poll_payment_refunds():
        from app.services.payment_service import PaymentService

        n = await PaymentService.poll_pending_refunds(limit=20)
        if n:
            logger.info("Payment refunds confirmed via query: %s", n)

    @scheduler.scheduled_job(
        "interval", hours=6, id="cleanup_visitor_data", name="清理访客临时数据"
    )
    @_leader_job
    async def cleanup_visitor_data():
        async with AsyncSessionLocal() as db:
            await db.execute(delete(VisitorEvent).where(VisitorEvent.expires_at <= naive_local_now()))
            await db.commit()
            logger.info("Expired visitor data cleaned")

    @scheduler.scheduled_job(
        "cron", hour=3, minute=0, timezone="Asia/Shanghai", id="cleanup_operation_logs", name="清理操作日志"
    )
    @_leader_job
    async def cleanup_operation_logs():
        async with AsyncSessionLocal() as db:
            from app.services.system_settings_service import SystemSettingsService

            days = await SystemSettingsService.get_int(db, "operation_log_retention_days", 90)
            cutoff = naive_local_now() - timedelta(days=days)
            await db.execute(delete(OperationLog).where(OperationLog.created_at < cutoff))
            await db.commit()
            logger.info("Operation logs cleaned before %s", cutoff)

    @scheduler.scheduled_job(
        "cron", hour=4, minute=0, timezone="Asia/Shanghai", id="cleanup_delivery_pii", name="外卖 PII 脱敏"
    )
    @_leader_job
    async def cleanup_delivery_pii():
        """P1: 外卖订单 PII 脱敏 — 已完结超 30 天的 recipient/rider 字段置 NULL。"""
        async with AsyncSessionLocal() as db:
            n = await anonymize_expired_delivery_pii(db)
        if n:
            logger.info("Delivery PII anonymized: %s orders", n)

    @scheduler.scheduled_job(
        "cron", hour=3, minute=30, timezone="Asia/Shanghai", id="maintain_file_logs", name="维护文件日志"
    )
    @_leader_job
    async def maintain_file_logs():
        """文件日志：7 天明文→gzip，满月打包，超过 90 天删除。"""
        from app.services.file_log_service import maintain_logs
        from app.services.scheduler_admin import redis_job_lock

        async with redis_job_lock("maintain_file_logs", ttl=600) as ok:
            if not ok:
                return
            stats = maintain_logs()
            if any(stats.values()):
                logger.info("File logs maintained: %s", stats)

    @scheduler.scheduled_job(
        "interval", seconds=30, id="drain_outbox_events", name="消费 Outbox 事件"
    )
    @_leader_job
    async def drain_outbox_events():
        """DB outbox worker：消费支付副作用/退款补偿等事务型事件。"""
        n = await process_pending_outbox(limit=30)
        if n:
            logger.info("Outbox events processed: %s", n)

    @scheduler.scheduled_job(
        "interval", minutes=5, id="reclaim_stale_outbox", name="回收卡住的 Outbox"
    )
    @_leader_job
    async def reclaim_stale_outbox():
        """回收崩溃残留的 PROCESSING outbox 行。"""
        async with AsyncSessionLocal() as db:
            n = await reclaim_stale_processing(db)
        if n:
            logger.info("Stale outbox PROCESSING reclaimed: %s", n)

    @scheduler.scheduled_job(
        "interval",
        minutes=5,
        id="reclaim_stale_webhook_inbox",
        name="回收卡住的 Webhook Inbox",
    )
    @_leader_job
    async def reclaim_stale_webhook_inbox():
        """回收崩溃残留的 PROCESSING webhook inbox 行。"""
        async with AsyncSessionLocal() as db:
            n = await reclaim_stale_inbox(db)
        if n:
            logger.info("Stale webhook inbox PROCESSING reclaimed: %s", n)

    @scheduler.scheduled_job(
        "cron", minute="*/2", id="reclaim_stale_printing_jobs", name="回收卡住的打印任务"
    )
    @_leader_job
    async def reclaim_stale_printing_jobs():
        """回收崩溃残留的 PRINTING 打印任务（locked_at > 5min 视为残留）。"""
        async with AsyncSessionLocal() as db:
            n = await PrintService.reclaim_stale_printing(db)
        if n:
            logger.info("Stale PRINTING print jobs reclaimed: %s", n)

    @scheduler.scheduled_job(
        "interval", minutes=4, id="learn_taste_profiles", name="口味画像增量学习"
    )
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

    @scheduler.scheduled_job(
        "interval", minutes=30, id="reencrypt_face_profiles", name="人脸向量密钥轮换"
    )
    @_leader_job
    async def reencrypt_face_profiles():
        """密钥轮换后：把旧 version 人脸向量重加密到当前密钥。"""
        from app.services.face_service import FaceService

        async with AsyncSessionLocal() as db:
            n = await FaceService.reencrypt_stale_profiles(db, limit=50)
            if n:
                await db.commit()
                logger.info("Face profiles re-encrypted: %s", n)

    @scheduler.scheduled_job(
        "cron",
        hour=3,
        minute=15,
        timezone="Asia/Shanghai",
        id="cleanup_expired_behavior_events",
        name="清理超期行为事件",
    )
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

    @scheduler.scheduled_job(
        "cron",
        hour=2,
        minute=10,
        timezone="Asia/Shanghai",
        id="night_data_batch",
        name="关店夜间数据批处理",
        misfire_grace_time=3600,
    )
    @_leader_job
    async def night_data_batch():
        """关店窗慢任务：销量热销/推荐、商品口味画像、顾客智能口味深度学习。"""
        from app.services.night_batch_service import run_night_batch
        from app.services.scheduler_admin import redis_job_lock

        async with redis_job_lock("night_data_batch", ttl=10_800) as acquired:
            if not acquired:
                logger.info("夜间批处理跳过：已有实例在跑")
                return
            async with AsyncSessionLocal() as db:
                try:
                    stats = await run_night_batch(db)
                    await db.commit()
                    if stats.get("ran"):
                        logger.info("夜间批处理完成: %s", stats)
                    else:
                        logger.info("夜间批处理未执行: %s", stats.get("skipped"))
                except Exception:
                    await db.rollback()
                    logger.exception("夜间批处理异常")


async def apply_scheduler_overrides(scheduler) -> None:
    """Leader 启动后：从 DB 加载覆盖并热更新启停/周期。"""
    from app.services.scheduler_admin import reload_overrides_to_scheduler

    await reload_overrides_to_scheduler(scheduler)


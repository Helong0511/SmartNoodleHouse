import logging
from datetime import timedelta

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.response import naive_local_now
from app.models import OperationLog, Order

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
        "description": "删除已过期 VisitorEvent 及 visitors/年/月/日 抓拍图。",
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
        "description": "北京时间默认 02:10 启动：按销量刷新热销/推荐、分类时段自动排序、整店重算餐品口味画像、深度学习顾客智能口味。",
        "default_schedule": {"type": "cron", "hour": 2, "minute": 10},
    },
    {
        "id": "sleep_member_wake",
        "title": "沉睡会员唤醒",
        "category": "营销",
        "description": "每日 10:05 按 30/90 天档给沉睡活跃会员发唤醒券（模板名配置后生效，发券幂等）。",
        "default_schedule": {"type": "cron", "hour": 10, "minute": 5},
    },
    {
        "id": "balance_low_remind",
        "title": "储值余额提醒",
        "category": "营销",
        "description": "每日 11:05 提醒余额偏低（>0 且低于阈值）的活跃会员，30 天一次（Redis 防重）。",
        "default_schedule": {"type": "cron", "hour": 11, "minute": 5},
    },
    {
        "id": "points_expire",
        "title": "积分过期清零",
        "category": "营销",
        "description": "每日 02:30 对超过 points_expire_days 未消费的 ACTIVE 会员积分整体清零（0=关闭）。",
        "default_schedule": {"type": "cron", "hour": 2, "minute": 30},
    },
    {
        "id": "marketing_task_worker",
        "title": "营销任务消费",
        "category": "营销",
        "description": "消费 marketing_tasks PENDING（ISSUE_COUPON），幂等 mkt: 前缀；并推进活动 SCHEDULED/ENDED。",
        "default_schedule": {"type": "interval", "unit": "seconds", "every": 30},
    },
    {
        "id": "reclaim_stale_marketing_tasks",
        "title": "回收卡住的营销任务",
        "category": "营销",
        "description": "回收超时仍为 RUNNING 的 marketing_tasks（复位 PENDING；超次 FAILED）。",
        "default_schedule": {"type": "interval", "unit": "minutes", "every": 5},
    },
    {
        "id": "social_love_day_issue",
        "title": "爱心日公益发券",
        "category": "营销",
        "description": "每日 09:10 若当日为爱心日星期且已配模板名，给 ACTIVE 公益身份发券（love_day: 幂等）。",
        "default_schedule": {"type": "cron", "hour": 9, "minute": 10},
    },
]


async def describe_scheduler_status(scheduler=None, db=None) -> dict:
    """供管理后台：目录 + 覆盖启停/周期 + 运行时 next_run + 最近执行情况。"""
    from app.core.config import get_settings
    from app.core.leader_lock import is_leader_held
    from app.services.scheduler_admin import (
        effective_schedule,
        format_scheduler_wall_time,
        format_trigger_label,
        load_all_job_stats,
        load_next_runs,
        load_overrides,
        normalize_scheduler_wall_time,
        save_next_runs,
    )

    settings = get_settings()
    overrides: dict = {}
    if db is not None:
        overrides = await load_overrides(db)

    runtime: dict[str, dict] = {}
    live_next: dict[str, str] = {}
    scheduler_running = False
    leader_held = bool(is_leader_held())
    if scheduler is not None:
        try:
            scheduler_running = bool(getattr(scheduler, "running", False))
            for job in scheduler.get_jobs():
                nxt = getattr(job, "next_run_time", None)
                wall = format_scheduler_wall_time(nxt)
                jid = str(job.id)
                if wall:
                    live_next[jid] = wall
                runtime[jid] = {
                    "next_run_at": wall,
                    "pending": bool(getattr(job, "pending", False)),
                    "paused": bool(nxt is None and scheduler_running),
                }
            # Leader 刷新时把下次执行写入 Redis，非 Leader 刷新也能看见
            if leader_held and scheduler_running and live_next:
                await save_next_runs(live_next)
        except Exception:
            logger.exception("读取 scheduler jobs 失败")

    cached_next = await load_next_runs() if not live_next else {}
    run_stats = await load_all_job_stats()

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
        st = run_stats.get(jid) or {}
        next_run_at = rt.get("next_run_at") or cached_next.get(jid)
        next_run_at = normalize_scheduler_wall_time(next_run_at) if next_run_at else None
        last_run_at = normalize_scheduler_wall_time(st.get("last_run_at")) if st.get("last_run_at") else None
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
                "next_run_at": next_run_at,
                "next_run_source": "live" if jid in live_next else ("cache" if next_run_at else None),
                "pending": rt.get("pending"),
                "registered": jid in runtime or bool(cached_next.get(jid)),
                "paused": paused or (jid in runtime and not enabled),
                "last_run_at": last_run_at,
                "last_status": st.get("last_status"),
                "last_duration_ms": st.get("last_duration_ms"),
                "last_error": st.get("last_error"),
                "ok_count": int(st.get("ok_count") or 0),
                "error_count": int(st.get("error_count") or 0),
            }
        )
    return {
        "process_role": settings.process_role,
        "leader_held": leader_held,
        "scheduler_running": scheduler_running,
        "note": "时间均为业务时区墙钟（默认 Asia/Shanghai）。下次/上次执行由 Leader 写入 Redis，任意进程刷新可见。",
        "total": len(items),
        "items": items,
    }


def register_tasks(scheduler) -> None:
    settings = get_settings()

    def _leader_job(fn):
        """调度任务 fence：内存旗标 + Redis 持锁校验，缩小双 Leader 窗口。"""

        async def _wrapped(*args, **kwargs):
            import time

            from app.core.leader_lock import confirm_leader_owned, is_leader_held
            from app.services.scheduler_admin import (
                JobSkipped,
                format_scheduler_wall_time,
                is_job_enabled,
                record_job_run,
                upsert_next_run,
            )

            if not is_leader_held():
                return None
            # 二次确认 Redis 锁值仍属本进程（TTL 过期换主后，续期循环最多延迟 RENEW_INTERVAL）
            if not await confirm_leader_owned():
                return None
            job_name = getattr(fn, "__name__", "")
            if job_name and not is_job_enabled(job_name):
                return None
            started = time.perf_counter()

            async def _refresh_next_run() -> None:
                if not job_name:
                    return
                try:
                    from app.main import scheduler as sched

                    job = sched.get_job(job_name) if sched is not None else None
                    nxt = getattr(job, "next_run_time", None) if job else None
                    await upsert_next_run(job_name, format_scheduler_wall_time(nxt))
                except Exception:
                    pass

            try:
                result = await fn(*args, **kwargs)
                if job_name:
                    await record_job_run(
                        job_name,
                        status="ok",
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                    await _refresh_next_run()
                return result
            except JobSkipped as skip:
                # 跨进程锁未拿到等主动跳过：勿记 ok
                if job_name:
                    await record_job_run(
                        job_name,
                        status="skipped",
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        error=skip.reason,
                    )
                    await _refresh_next_run()
                return None
            except Exception as exc:
                if job_name:
                    await record_job_run(
                        job_name,
                        status="error",
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        error=str(exc) or exc.__class__.__name__,
                    )
                    await _refresh_next_run()
                raise

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
        from app.services.scheduler_admin import exclusive_scheduler_job

        async with exclusive_scheduler_job("refresh_platform_tokens", ttl=600):
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
        from app.services.scheduler_admin import exclusive_scheduler_job

        async with exclusive_scheduler_job("auto_open_close_delivery_stores", ttl=90):
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
        from app.services.scheduler_admin import exclusive_scheduler_job

        async with exclusive_scheduler_job("delivery_stock_reconcile", ttl=240):
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
        """支付超时关单：逐单短事务，避免一次锁住多单再串行调微信/支付宝。"""
        from app.services.order_lifecycle import apply_order_status
        from app.services.payment_service import PaymentService
        from app.services.system_settings_service import SystemSettingsService
        from app.services.websocket_manager import ws_manager

        async with AsyncSessionLocal() as db:
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
            # 只捞 id，不加长锁（高并发多顾客互不拖累）
            order_ids = list(
                (
                    await db.execute(
                        select(Order.id)
                        .where(
                            Order.status == "PENDING_PAYMENT",
                            Order.payment_status == "UNPAID",
                            Order.created_at <= timeout,
                        )
                        .order_by(Order.id.asc())
                        .limit(80)
                    )
                )
                .scalars()
                .all()
            )

        closed = 0
        rescued = 0
        now = naive_local_now()
        for oid in order_ids:
            try:
                # 1) 短锁确认仍超时未付，立刻释放（渠道 HTTP 不得持订单锁）
                order_no = None
                store_id = None
                async with AsyncSessionLocal() as db:
                    order = (
                        await db.execute(
                            select(Order)
                            .where(
                                Order.id == int(oid),
                                Order.status == "PENDING_PAYMENT",
                                Order.payment_status == "UNPAID",
                            )
                            .with_for_update(skip_locked=True)
                        )
                    ).scalar_one_or_none()
                    if not order:
                        continue
                    minutes = store_timeouts.get(int(order.store_id), global_timeout)
                    if minutes <= 0:
                        await db.commit()
                        continue
                    if order.created_at and order.created_at > now - timedelta(minutes=minutes):
                        await db.commit()
                        continue
                    order_no = order.order_no
                    store_id = order.store_id
                    await db.commit()

                # 2) 锁外查单
                try:
                    if await PaymentService.sync_before_close_order_by_id(int(oid)):
                        rescued += 1
                        try:
                            await ws_manager.notify_order_event(
                                "ORDER_PAID",
                                {
                                    "order_id": int(oid),
                                    "order_no": order_no,
                                    "store_id": store_id,
                                    "source": "CLOSE_PRE_SYNC",
                                },
                            )
                        except Exception:
                            logger.exception(
                                "关单前入账 WS 通知失败 order_id=%s", oid
                            )
                        continue
                except Exception:
                    logger.exception("关单前查单异常 order_id=%s", oid)

                # 3) 锁外撤销渠道 + 再查单
                try:
                    if await PaymentService.close_channel_payments_by_id(int(oid)):
                        rescued += 1
                        try:
                            await ws_manager.notify_order_event(
                                "ORDER_PAID",
                                {
                                    "order_id": int(oid),
                                    "order_no": order_no,
                                    "store_id": store_id,
                                    "source": "CLOSE_CHANNEL_LATE_PAID",
                                },
                            )
                        except Exception:
                            logger.exception(
                                "关单迟到入账 WS 通知失败 order_id=%s", oid
                            )
                        continue
                except Exception:
                    logger.exception("关单撤销渠道交易异常 order_id=%s", oid)

                # 4) 短锁关单（仍未付才取消）
                async with AsyncSessionLocal() as db:
                    order = (
                        await db.execute(
                            select(Order)
                            .where(
                                Order.id == int(oid),
                                Order.status == "PENDING_PAYMENT",
                                Order.payment_status == "UNPAID",
                            )
                            .with_for_update(skip_locked=True)
                        )
                    ).scalar_one_or_none()
                    if not order:
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
                    await db.commit()
                    closed += 1
            except Exception:
                logger.exception("超时关单处理异常 order_id=%s", oid)

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
        from app.services.face_service import VisitorService
        from app.services.scheduler_admin import exclusive_scheduler_job

        async with exclusive_scheduler_job("cleanup_visitor_data", ttl=600):
            async with AsyncSessionLocal() as db:
                stats = await VisitorService.cleanup_expired(db)
                logger.info(
                    "Expired visitor data cleaned rows=%s files=%s dirs=%s",
                    stats.get("deleted_rows"),
                    stats.get("deleted_files"),
                    stats.get("pruned_dirs"),
                )

    @scheduler.scheduled_job(
        "cron", hour=3, minute=0, timezone="Asia/Shanghai", id="cleanup_operation_logs", name="清理操作日志"
    )
    @_leader_job
    async def cleanup_operation_logs():
        from app.services.scheduler_admin import exclusive_scheduler_job
        from app.services.system_settings_service import SystemSettingsService

        async with exclusive_scheduler_job("cleanup_operation_logs", ttl=600):
            async with AsyncSessionLocal() as db:
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
        from app.services.scheduler_admin import exclusive_scheduler_job

        async with exclusive_scheduler_job("cleanup_delivery_pii", ttl=600):
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
        from app.services.scheduler_admin import exclusive_scheduler_job

        async with exclusive_scheduler_job("maintain_file_logs", ttl=600):
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
        "interval",
        minutes=5,
        id="reclaim_stale_marketing_tasks",
        name="回收卡住的营销任务",
    )
    @_leader_job
    async def reclaim_stale_marketing_tasks():
        """回收崩溃残留的 RUNNING marketing_tasks。"""
        from app.services.marketing_service import MarketingService

        async with AsyncSessionLocal() as db:
            stats = await MarketingService.reclaim_stale_running(db)
            await db.commit()
        if stats.get("requeued") or stats.get("failed"):
            logger.info("Stale marketing RUNNING reclaimed: %s", stats)

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
        from app.services.scheduler_admin import exclusive_scheduler_job
        from app.services.taste_learning_service import TasteLearningService

        async with exclusive_scheduler_job("learn_taste_profiles", ttl=240):
            async with AsyncSessionLocal() as db:
                since = naive_local_now() - timedelta(minutes=15)
                try:
                    stats = await TasteLearningService.learn_recent_customers(
                        db, since=since, limit=200
                    )
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
        from app.services.scheduler_admin import exclusive_scheduler_job

        async with exclusive_scheduler_job("reencrypt_face_profiles", ttl=600):
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
        from app.services.scheduler_admin import exclusive_scheduler_job
        from app.services.system_settings_service import SystemSettingsService

        async with exclusive_scheduler_job("cleanup_expired_behavior_events", ttl=600):
            async with AsyncSessionLocal() as db:
                days = await SystemSettingsService.get_int(
                    db, "behavior_event_retention_days", 90
                )
                n = await PrivacyService.cleanup_expired_events(db, retention_days=days)
            if n:
                logger.info(
                    "Expired behavior events cleaned: %s rows (retention=%sd)", n, days
                )

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
        """关店窗慢任务：销量热销/推荐、分类时段自动排序、餐品口味画像、顾客智能口味深度学习。"""
        from app.services.night_batch_service import run_night_batch
        from app.services.scheduler_admin import JobSkipped, exclusive_scheduler_job

        try:
            async with exclusive_scheduler_job("night_data_batch", ttl=10_800):
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
        except JobSkipped:
            logger.info("夜间批处理跳过：已有实例在跑")
            raise

    @scheduler.scheduled_job(
        "cron",
        hour=10,
        minute=5,
        timezone="Asia/Shanghai",
        id="sleep_member_wake",
        name="沉睡会员唤醒",
        misfire_grace_time=3600,
    )
    @_leader_job
    async def sleep_member_wake():
        """每日 10:05 沉睡会员唤醒：按 30/90 天档给活跃会员发唤醒券（模板名空=关闭）。"""
        from app.services import retention_service
        from app.services.scheduler_admin import exclusive_scheduler_job

        async with exclusive_scheduler_job("sleep_member_wake", ttl=3600):
            async with AsyncSessionLocal() as db:
                try:
                    await retention_service.run_sleep_wake(db)
                except Exception:
                    await db.rollback()
                    logger.exception("沉睡会员唤醒任务异常")

    @scheduler.scheduled_job(
        "cron",
        hour=11,
        minute=5,
        timezone="Asia/Shanghai",
        id="balance_low_remind",
        name="储值余额提醒",
        misfire_grace_time=3600,
    )
    @_leader_job
    async def balance_low_remind():
        """每日 11:05 储值余额提醒：余额偏低（>0 且低于阈值）的活跃会员 30 天提醒一次。"""
        from app.services import retention_service
        from app.services.scheduler_admin import exclusive_scheduler_job

        async with exclusive_scheduler_job("balance_low_remind", ttl=3600):
            async with AsyncSessionLocal() as db:
                try:
                    await retention_service.run_balance_low_remind(db)
                except Exception:
                    await db.rollback()
                    logger.exception("储值余额提醒任务异常")

    @scheduler.scheduled_job(
        "cron",
        hour=2,
        minute=30,
        timezone="Asia/Shanghai",
        id="points_expire",
        name="积分过期清零",
        misfire_grace_time=3600,
    )
    @_leader_job
    async def points_expire():
        """每日 02:30 积分过期清零：超期未消费的 ACTIVE 会员积分整体清零。"""
        from app.services import retention_service
        from app.services.scheduler_admin import exclusive_scheduler_job

        async with exclusive_scheduler_job("points_expire", ttl=3600):
            async with AsyncSessionLocal() as db:
                try:
                    stats = await retention_service.run_points_expire(db)
                    if stats.get("expired_count"):
                        logger.info("积分过期清零: %s", stats)
                except Exception:
                    await db.rollback()
                    logger.exception("积分过期清零任务异常")

    @scheduler.scheduled_job(
        "interval",
        seconds=30,
        id="marketing_task_worker",
        name="营销任务消费",
        misfire_grace_time=60,
    )
    @_leader_job
    async def marketing_task_worker():
        """消费营销 PENDING 任务：只调 CouponService；活动生命周期推进。"""
        from app.services.marketing_service import MarketingService
        from app.services.scheduler_admin import exclusive_scheduler_job

        # 任务锁 + 行级 claim；process 内单条 commit，崩溃不回滚已成功条目
        async with exclusive_scheduler_job("marketing_task_worker", ttl=90):
            async with AsyncSessionLocal() as db:
                try:
                    stats = await MarketingService.process_pending_tasks(db, limit=50)
                    # 各条已自行 commit；此处兜底提交生命周期等残留
                    await db.commit()
                    if stats.get("processed"):
                        logger.info("营销任务消费: %s", stats)
                except Exception:
                    await db.rollback()
                    logger.exception("营销任务消费异常")

    @scheduler.scheduled_job(
        "cron",
        hour=9,
        minute=10,
        id="social_love_day_issue",
        name="爱心日公益发券",
        misfire_grace_time=3600,
    )
    @_leader_job
    async def social_love_day_issue():
        """爱心日：ACTIVE 公益身份发豆浆券（模板名配置后生效）。"""
        from app.services.scheduler_admin import exclusive_scheduler_job
        from app.services.social_benefit_service import SocialBenefitService

        async with exclusive_scheduler_job("social_love_day_issue", ttl=3600):
            async with AsyncSessionLocal() as db:
                try:
                    stats = await SocialBenefitService.run_love_day_issue(db)
                    await db.commit()
                    if not stats.get("skipped"):
                        logger.info("爱心日发券: %s", stats)
                except Exception:
                    await db.rollback()
                    logger.exception("爱心日发券异常")


async def apply_scheduler_overrides(scheduler) -> None:
    """Leader 启动后：从 DB 加载覆盖并热更新启停/周期。"""
    from app.services.scheduler_admin import reload_overrides_to_scheduler

    await reload_overrides_to_scheduler(scheduler)


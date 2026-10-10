"""会员留存营销定时任务：沉睡会员唤醒发券 + 储值余额偏低提醒。

设计要点：
- 发券幂等由 CouponService.issue_from_template 的 client_request_id 保证
  （WELCOME:{customer}:{template_id}，每顾客每模板至多一张），无需额外防重；
- 每批默认 500 人、按 id 分批提交，避免超大事务；
- 余额提醒防重走 Redis SET NX EX（30 天一次），Redis 异常 fail-open 跳过本轮；
- 本期提醒动作仅记录日志，订阅消息发送留待后续接入。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import naive_local_now
from app.core.redis_client import try_get_redis
from app.models import Customer
from app.services.coupon_service import CouponService
from app.services.system_settings_service import SystemSettingsService

logger = logging.getLogger(__name__)

# SystemSetting 键：空/缺失 = 该档关闭
SETTING_SLEEP_WAKE_COUPON_30 = "sleep_wake_coupon_name_30"
SETTING_SLEEP_WAKE_COUPON_90 = "sleep_wake_coupon_name_90"
SETTING_BALANCE_LOW_THRESHOLD = "balance_low_threshold"

# 余额提醒防重：30 天提醒一次
BALANCE_NOTIFY_KEY_FMT = "snh:notify:balance_low:{customer_id}"
BALANCE_NOTIFY_TTL_SECONDS = 30 * 24 * 3600

DEFAULT_BATCH_SIZE = 500


async def _batched_active_customers(
    db: AsyncSession, *conditions, batch_size: int = DEFAULT_BATCH_SIZE
):
    """按 id 升序分批（keyset 分页）扫描满足条件的顾客，每批 commit 由调用方负责。"""
    last_id = 0
    while True:
        rows = list(
            (
                await db.execute(
                    select(Customer)
                    .where(Customer.id > last_id, *conditions)
                    .order_by(Customer.id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return
        yield rows
        last_id = int(rows[-1].id)
        if len(rows) < batch_size:
            return


async def _wake_tier(
    db: AsyncSession,
    template_name: str,
    *,
    upper_exclusive: datetime,
    lower_inclusive: datetime | None,
    batch_size: int,
) -> int:
    """对一档沉睡顾客按模板发券，返回实际发券人数。

    30 天档：lower_inclusive=now-90d, upper_exclusive=now-30d；
    90 天档：lower_inclusive=None, upper_exclusive=now-90d。
    微信/支付宝账号绑定仅为触达偏好，不做强制过滤。
    """
    conditions = [
        Customer.last_order_at.isnot(None),
        Customer.last_order_at < upper_exclusive,
        Customer.account_status == "ACTIVE",
    ]
    if lower_inclusive is not None:
        conditions.append(Customer.last_order_at >= lower_inclusive)

    issued = 0
    async for rows in _batched_active_customers(db, *conditions, batch_size=batch_size):
        for customer in rows:
            coupon = await CouponService.issue_from_template(
                db,
                int(customer.id),
                template_name,
                store_id=customer.registered_store_id,
            )
            if coupon is not None:
                issued += 1
        await db.commit()
    return issued


async def run_sleep_wake(
    db: AsyncSession, *, now: datetime | None = None, batch_size: int = DEFAULT_BATCH_SIZE
) -> dict:
    """沉睡会员唤醒：按 30/90 天档给活跃会员发唤醒券（模板名空/缺失 = 关闭）。

    last_order_at IS NULL 的注册顾客无消费记录，不发券。
    """
    now = now or naive_local_now()
    name_30 = (
        (await SystemSettingsService.get_value(db, SETTING_SLEEP_WAKE_COUPON_30, "")) or ""
    ).strip()
    name_90 = (
        (await SystemSettingsService.get_value(db, SETTING_SLEEP_WAKE_COUPON_90, "")) or ""
    ).strip()

    stats: dict = {
        "enabled_30": bool(name_30),
        "enabled_90": bool(name_90),
        "template_30": name_30 or None,
        "template_90": name_90 or None,
        "issued_30": 0,
        "issued_90": 0,
    }
    if name_30:
        stats["issued_30"] = await _wake_tier(
            db,
            name_30,
            upper_exclusive=now - timedelta(days=30),
            lower_inclusive=now - timedelta(days=90),
            batch_size=batch_size,
        )
    if name_90:
        stats["issued_90"] = await _wake_tier(
            db,
            name_90,
            upper_exclusive=now - timedelta(days=90),
            lower_inclusive=None,
            batch_size=batch_size,
        )
    logger.info(
        "沉睡会员唤醒完成: 30天档发券 %s 人（模板 %s）、90天档发券 %s 人（模板 %s）",
        stats["issued_30"],
        stats["template_30"] or "未配置",
        stats["issued_90"],
        stats["template_90"] or "未配置",
    )
    return stats


async def run_balance_low_remind(
    db: AsyncSession, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> dict:
    """储值余额偏低提醒：0 < 余额 < 阈值 的活跃会员，30 天提醒一次。

    阈值 0/缺失/非法 = 关闭；Redis 不可用或异常时 fail-open 跳过本轮。
    本期仅记录提醒清单日志，订阅消息发送留待后续接入。
    """
    raw = (
        (await SystemSettingsService.get_value(db, SETTING_BALANCE_LOW_THRESHOLD, "0")) or ""
    ).strip()
    try:
        threshold = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        threshold = Decimal("0")

    stats: dict = {
        "enabled": threshold > 0,
        "threshold": str(threshold) if threshold > 0 else None,
        "reminded": 0,
        "customer_ids": [],
        "skipped": None,
    }
    if threshold <= 0:
        return stats

    r = await try_get_redis()
    if r is None:
        stats["skipped"] = "redis_unavailable"
        logger.warning("储值余额提醒跳过本轮：Redis 不可用（fail-open，不阻塞）")
        return stats

    total_balance = Customer.balance_principal + Customer.balance_gift
    conditions = [
        Customer.account_status == "ACTIVE",
        total_balance > 0,
        total_balance < threshold,
    ]
    last_id = 0
    while True:
        rows = list(
            (
                await db.execute(
                    select(Customer)
                    .where(Customer.id > last_id, *conditions)
                    .order_by(Customer.id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            break
        for customer in rows:
            key = BALANCE_NOTIFY_KEY_FMT.format(customer_id=int(customer.id))
            try:
                set_ok = bool(await r.set(key, "1", nx=True, ex=BALANCE_NOTIFY_TTL_SECONDS))
            except Exception as exc:
                # Redis 中途异常：跳过本轮剩余顾客（fail-open），下次再提醒
                stats["skipped"] = "redis_error"
                logger.warning("储值余额提醒 Redis 异常，本轮中断: %s", exc)
                return stats
            if not set_ok:
                continue  # 30 天内已提醒过
            stats["reminded"] += 1
            stats["customer_ids"].append(int(customer.id))
        last_id = int(rows[-1].id)
        if len(rows) < batch_size:
            break

    if stats["reminded"]:
        logger.info(
            "储值余额提醒: %s 人余额低于阈值 %s 元（30 天仅提醒一次）: %s",
            stats["reminded"],
            threshold,
            stats["customer_ids"][:50],
        )
    return stats

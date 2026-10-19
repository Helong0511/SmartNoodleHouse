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
from app.models import Customer, PointLedger
from app.services.coupon_service import CouponService
from app.services.customer_service import CustomerService
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
            try:
                coupon = await CouponService.issue_from_template(
                    db,
                    int(customer.id),
                    template_name,
                    store_id=customer.registered_store_id,
                )
                # 单客 commit：长批次崩溃时已发券不回滚（模板侧 WELCOME 幂等）
                await db.commit()
                if coupon is not None:
                    issued += 1
            except Exception:
                await db.rollback()
                logger.exception(
                    "沉睡唤醒单客发券失败 customer_id=%s template=%s",
                    getattr(customer, "id", None),
                    template_name,
                )
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


async def _send_balance_low_to_customer(
    db: AsyncSession,
    customer: Customer,
    *,
    threshold: Decimal,
) -> str:
    """尝试微信/支付宝订阅并写 marketing_message_logs。返回 SUCCESS/FAILED/SKIPPED/LOG。"""
    from app.models import CustomerAlipayAccount, CustomerWechatAccount, MarketingMessageLog
    from app.services.alipay_mini_client import (
        send_balance_low_subscribe as send_alipay_balance_low,
    )
    from app.services.wechat_mini_client import send_balance_low_subscribe

    bal = (customer.balance_principal or Decimal("0")) + (customer.balance_gift or Decimal("0"))
    bal_s = f"{bal:.2f}"
    thr_s = f"{threshold:.2f}"
    day = naive_local_now().strftime("%Y%m%d")
    idem = f"balance_low:{int(customer.id)}:{day}"
    exists = await db.scalar(
        select(MarketingMessageLog.id).where(MarketingMessageLog.idempotency_key == idem)
    )
    if exists:
        return "SKIPPED"

    wx = await db.scalar(
        select(CustomerWechatAccount).where(CustomerWechatAccount.customer_id == int(customer.id)).limit(1)
    )
    openid = (wx.openid if wx else None) or None
    alipay_row = await db.scalar(
        select(CustomerAlipayAccount)
        .where(CustomerAlipayAccount.customer_id == int(customer.id))
        .limit(1)
    )
    alipay_uid = (alipay_row.alipay_user_id if alipay_row else None) or None

    wx_tmpl = (
        await SystemSettingsService.effective_setting(
            db, "wechat_subscribe_template_balance_low", fallback=""
        )
        or ""
    ).strip()
    alipay_tmpl = (
        await SystemSettingsService.effective_setting(
            db, "alipay_subscribe_template_balance_low", fallback=""
        )
        or ""
    ).strip()

    status = "LOG"
    err = None
    channel = "LOG"
    tmpl_used = ""
    wx_ok = False
    alipay_ok = False
    store_id = customer.registered_store_id
    customer_id = int(customer.id)

    # 读完身份后释放会话，再调微信/支付宝（调用方 Redis nx 已防并发双发）
    await db.commit()

    if wx_tmpl and openid:
        channel = "WECHAT"
        tmpl_used = wx_tmpl
        try:
            wx_ok = await send_balance_low_subscribe(
                openid=str(openid),
                customer_id=customer_id,
                balance=bal_s,
                threshold=thr_s,
            )
        except Exception as exc:
            err = str(exc)[:400]

    if alipay_tmpl and alipay_uid:
        try:
            alipay_ok = await send_alipay_balance_low(
                alipay_user_id=str(alipay_uid),
                customer_id=customer_id,
                balance=bal_s,
                threshold=thr_s,
            )
            if alipay_ok and not wx_ok:
                channel = "ALIPAY"
                tmpl_used = alipay_tmpl
            elif alipay_ok and wx_ok:
                channel = "WECHAT+ALIPAY"
                tmpl_used = f"{wx_tmpl}|{alipay_tmpl}"
            elif not tmpl_used:
                tmpl_used = alipay_tmpl
                channel = "ALIPAY"
        except Exception as exc:
            if not err:
                err = str(exc)[:400]

    if wx_ok or alipay_ok:
        status = "SUCCESS"
        err = None
    elif (wx_tmpl and openid) or (alipay_tmpl and alipay_uid):
        status = "FAILED"
        if not err:
            err = "subscribe_send_failed"
    elif not wx_tmpl and not alipay_tmpl:
        status = "LOG"
        err = "template_not_configured"
    else:
        status = "SKIPPED"
        err = "no_channel_identity"

    db.add(
        MarketingMessageLog(
            store_id=store_id,
            campaign_id=None,
            customer_id=customer_id,
            channel=channel,
            template_key="balance_low",
            template_id=tmpl_used or None,
            payload_json={"balance": bal_s, "threshold": thr_s},
            status=status,
            error_message=err,
            idempotency_key=idem,
            sent_at=naive_local_now() if status in ("SUCCESS", "LOG") else None,
        )
    )
    await db.flush()
    return status


async def run_balance_low_remind(
    db: AsyncSession, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> dict:
    """储值余额偏低提醒：0 < 余额 < 阈值 的活跃会员，30 天提醒一次。

    阈值 0/缺失/非法 = 关闭；Redis 不可用或异常时 fail-open 跳过本轮。
    配置 wechat_subscribe_template_balance_low 后真正发订阅；否则写 LOG 触达记录。
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
        "sent_success": 0,
        "sent_failed": 0,
        "logged_only": 0,
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
                stats["skipped"] = "redis_error"
                logger.warning("储值余额提醒 Redis 异常，本轮中断: %s", exc)
                return stats
            if not set_ok:
                continue
            try:
                result = await _send_balance_low_to_customer(db, customer, threshold=threshold)
                # 单客 commit：推送/写日志落库后再进入下一人
                await db.commit()
            except Exception:
                await db.rollback()
                logger.exception(
                    "储值余额提醒单客失败 customer_id=%s", getattr(customer, "id", None)
                )
                continue
            stats["reminded"] += 1
            stats["customer_ids"].append(int(customer.id))
            if result == "SUCCESS":
                stats["sent_success"] += 1
            elif result == "FAILED":
                stats["sent_failed"] += 1
            else:
                stats["logged_only"] += 1
        last_id = int(rows[-1].id)
        if len(rows) < batch_size:
            break

    if stats["reminded"]:
        logger.info(
            "储值余额提醒: %s 人（成功推送 %s / 失败 %s / 仅日志 %s），阈值 %s 元",
            stats["reminded"],
            stats["sent_success"],
            stats["sent_failed"],
            stats["logged_only"],
            threshold,
        )
    return stats


SETTING_POINTS_EXPIRE_DAYS = "points_expire_days"


async def run_points_expire(
    db: AsyncSession, *, now: datetime | None = None, batch_size: int = DEFAULT_BATCH_SIZE
) -> dict:
    """积分过期清零：last_order_at 超期的 ACTIVE 会员积分整体清零，写 EXPIRE 流水。

    配置 points_expire_days（0=永不过期，跳过）。
    last_order_at IS NULL 的注册顾客（无消费记录）不处理。
    """
    now = now or naive_local_now()
    days = await SystemSettingsService.get_int(db, SETTING_POINTS_EXPIRE_DAYS, 365)

    stats: dict = {
        "enabled": days > 0,
        "expire_days": days if days > 0 else None,
        "expired_count": 0,
        "expired_points": 0,
        "skipped": None,
    }
    if days <= 0:
        return stats

    cutoff = now - timedelta(days=days)
    conditions = [
        Customer.last_order_at.isnot(None),
        Customer.last_order_at < cutoff,
        Customer.account_status == "ACTIVE",
        Customer.points > 0,
    ]

    async for rows in _batched_active_customers(db, *conditions, batch_size=batch_size):
        for customer in rows:
            try:
                # 行锁刷新当前积分（分批 query 可能已过期）
                locked = (
                    await db.execute(
                        select(Customer).where(Customer.id == int(customer.id)).with_for_update()
                    )
                ).scalar_one_or_none()
                if not locked or int(locked.points or 0) <= 0:
                    await db.rollback()
                    continue
                before = int(locked.points)
                locked.points = 0
                db.add(
                    PointLedger(
                        customer_id=locked.id,
                        order_id=None,
                        refund_id=None,
                        delta=-before,
                        balance_after=0,
                        entry_type="EXPIRE",
                    )
                )
                await CustomerService.recalculate_member_level(db, locked)
                # 单客 commit：释放行锁并落库，避免长事务与整批回滚
                await db.commit()
                stats["expired_count"] += 1
                stats["expired_points"] += before
            except Exception:
                await db.rollback()
                logger.exception(
                    "积分过期单客清零失败 customer_id=%s", getattr(customer, "id", None)
                )

    if stats["expired_count"]:
        logger.info(
            "积分过期清零: %s 人，共 %s 积分（过期阈值 %s 天）",
            stats["expired_count"],
            stats["expired_points"],
            days,
        )
    return stats

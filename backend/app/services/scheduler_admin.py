"""定时任务管理：覆盖项持久化 + APScheduler 热更新。"""
from __future__ import annotations

import json
import logging
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSetting

logger = logging.getLogger(__name__)

OVERRIDES_KEY = "scheduler_job_overrides"

# 内存缓存：供 _leader_job 快速判断 enabled（与 DB 覆盖同步）
_enabled_cache: dict[str, bool] = {}


def is_job_enabled(job_id: str) -> bool:
    """未出现在缓存里视为启用（默认开）。"""
    return _enabled_cache.get(job_id, True)


def set_enabled_cache(overrides: dict[str, Any] | None) -> None:
    global _enabled_cache
    cache: dict[str, bool] = {}
    for jid, ov in (overrides or {}).items():
        if isinstance(ov, dict) and "enabled" in ov:
            cache[str(jid)] = bool(ov.get("enabled"))
    _enabled_cache = cache


def format_trigger_label(schedule: dict[str, Any] | None) -> str:
    if not schedule:
        return "—"
    t = schedule.get("type")
    if t == "interval":
        every = int(schedule.get("every") or 1)
        unit = str(schedule.get("unit") or "minutes")
        unit_label = {"seconds": "秒", "minutes": "分钟", "hours": "小时"}.get(unit, unit)
        return f"每 {every} {unit_label}"
    if t == "cron":
        hour = int(schedule.get("hour") or 0)
        minute = int(schedule.get("minute") or 0)
        return f"每天 {hour:02d}:{minute:02d}"
    if t == "cron_interval":
        every = int(schedule.get("every_minutes") or 1)
        return f"每 {every} 分钟"
    return "—"


def validate_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化 schedule；非法则抛 ValueError。"""
    if not isinstance(schedule, dict):
        raise ValueError("schedule 须为对象")
    t = schedule.get("type")
    if t == "interval":
        unit = str(schedule.get("unit") or "").strip()
        if unit not in ("seconds", "minutes", "hours"):
            raise ValueError("interval.unit 须为 seconds/minutes/hours")
        every = int(schedule.get("every") or 0)
        bounds = {"seconds": (1, 86400), "minutes": (1, 1440), "hours": (1, 168)}
        lo, hi = bounds[unit]
        if every < lo or every > hi:
            raise ValueError(f"interval.every 须在 {lo}~{hi}")
        return {"type": "interval", "unit": unit, "every": every}
    if t == "cron":
        hour = int(schedule.get("hour") if schedule.get("hour") is not None else -1)
        minute = int(schedule.get("minute") if schedule.get("minute") is not None else -1)
        if hour < 0 or hour > 23:
            raise ValueError("cron.hour 须在 0~23")
        if minute < 0 or minute > 59:
            raise ValueError("cron.minute 须在 0~59")
        return {"type": "cron", "hour": hour, "minute": minute}
    if t == "cron_interval":
        every = int(schedule.get("every_minutes") or 0)
        if every < 1 or every > 60:
            raise ValueError("cron_interval.every_minutes 须在 1~60")
        return {"type": "cron_interval", "every_minutes": every}
    raise ValueError("不支持的 schedule.type")


def build_trigger(schedule: dict[str, Any]):
    s = validate_schedule(schedule)
    if s["type"] == "interval":
        unit = s["unit"]
        every = s["every"]
        kwargs = {unit: every}
        return IntervalTrigger(**kwargs)
    if s["type"] == "cron":
        return CronTrigger(hour=s["hour"], minute=s["minute"])
    if s["type"] == "cron_interval":
        return CronTrigger(minute=f"*/{s['every_minutes']}")
    raise ValueError("无法构建 trigger")


async def load_overrides(db: AsyncSession) -> dict[str, Any]:
    row = await db.scalar(
        select(SystemSetting).where(SystemSetting.setting_key == OVERRIDES_KEY)
    )
    if not row or not (row.setting_value or "").strip():
        set_enabled_cache({})
        return {}
    try:
        data = json.loads(row.setting_value)
        if not isinstance(data, dict):
            return {}
        set_enabled_cache(data)
        return data
    except Exception:
        logger.exception("解析 %s 失败", OVERRIDES_KEY)
        return {}


async def save_overrides(db: AsyncSession, overrides: dict[str, Any]) -> None:
    raw = json.dumps(overrides, ensure_ascii=False, separators=(",", ":"))
    row = await db.scalar(
        select(SystemSetting).where(SystemSetting.setting_key == OVERRIDES_KEY)
    )
    if row:
        row.setting_value = raw
        row.description = "定时任务启停与周期覆盖（JSON）"
    else:
        db.add(
            SystemSetting(
                setting_key=OVERRIDES_KEY,
                setting_value=raw,
                description="定时任务启停与周期覆盖（JSON）",
            )
        )
    await db.flush()
    set_enabled_cache(overrides)
    try:
        from app.services.system_settings_service import SystemSettingsService

        await SystemSettingsService.invalidate_cache()
    except Exception:
        logger.exception("刷新 system_settings 缓存失败")


def effective_schedule(meta: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    ov = override or {}
    if isinstance(ov.get("schedule"), dict):
        return validate_schedule(ov["schedule"])
    return dict(meta.get("default_schedule") or {})


def apply_job_to_scheduler(
    scheduler,
    job_id: str,
    *,
    schedule: dict[str, Any],
    enabled: bool,
) -> None:
    """对已注册 job 热更新周期与启停。调度器未运行时忽略。"""
    if scheduler is None or not getattr(scheduler, "running", False):
        return
    job = scheduler.get_job(job_id)
    if job is None:
        logger.warning("scheduler 无此任务，跳过热更新: %s", job_id)
        return
    trigger = build_trigger(schedule)
    scheduler.reschedule_job(job_id, trigger=trigger)
    if enabled:
        scheduler.resume_job(job_id)
    else:
        scheduler.pause_job(job_id)


def apply_all_overrides(scheduler, catalog: list[dict], overrides: dict[str, Any]) -> None:
    for meta in catalog:
        jid = meta["id"]
        ov = overrides.get(jid) if isinstance(overrides.get(jid), dict) else {}
        schedule = effective_schedule(meta, ov)
        enabled = bool(ov.get("enabled", True)) if ov else True
        try:
            apply_job_to_scheduler(scheduler, jid, schedule=schedule, enabled=enabled)
        except Exception:
            logger.exception("应用定时任务覆盖失败 job_id=%s", jid)

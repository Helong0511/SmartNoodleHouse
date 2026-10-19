"""定时任务管理：覆盖项持久化 + APScheduler 热更新 + 跨进程通知。"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSetting

logger = logging.getLogger(__name__)

OVERRIDES_KEY = "scheduler_job_overrides"
OVERRIDES_VER_KEY = "snh:scheduler:overrides:ver"
OVERRIDES_CHANNEL = "snh:scheduler:overrides"
JOB_STATS_KEY = "snh:scheduler:job_stats"
NEXT_RUNS_KEY = "snh:scheduler:next_runs"

# Redis 不可用时的进程内兜底（仅本进程可见）
_job_stats_mem: dict[str, dict[str, Any]] = {}
_next_runs_mem: dict[str, str] = {}


def format_scheduler_wall_time(dt: Any) -> str | None:
    """统一为业务时区墙钟：YYYY-MM-DD HH:mm:ss（供后台表格直接展示）。"""
    if dt is None:
        return None
    from datetime import datetime

    from app.core.response import ensure_local_aware

    if not isinstance(dt, datetime):
        return None
    aware = ensure_local_aware(dt)
    if aware is None:
        return None
    return aware.strftime("%Y-%m-%d %H:%M:%S")


def normalize_scheduler_wall_time(raw: Any) -> str | None:
    """把 ISO / 墙钟字符串规范成 YYYY-MM-DD HH:mm:ss（业务时区）。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) >= 19 and s[4] == "-" and s[10] in (" ", "T") and s[13] == ":":
        # 已是墙钟且无偏移：直接截断到秒
        head = s[:19].replace("T", " ")
        if len(s) == 19 or (len(s) > 19 and s[19] in (".", " ")):
            # 无时区：按业务本地墙钟信任
            if "+" not in s and not s.endswith("Z") and "z" not in s.lower():
                return head
    from datetime import datetime

    from app.core.response import ensure_local_aware

    try:
        # 兼容 2026-10-19 14:00:00+08:00 / ISO
        iso = s.replace(" ", "T", 1) if " " in s[:11] and "T" not in s[:11] else s
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return s[:19].replace("T", " ") if len(s) >= 19 else s
    return format_scheduler_wall_time(ensure_local_aware(dt))

# Lua：仅当值仍为本 token 时删除，避免误删他人锁
_LUA_COMPARE_DELETE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

# 内存缓存：供 _leader_job 快速判断 enabled（与 DB 覆盖同步）
_enabled_cache: dict[str, bool] = {}
_overrides_watch_task: asyncio.Task | None = None
_local_overrides_ver: int = 0


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


async def record_job_run(
    job_id: str,
    *,
    status: str,
    duration_ms: int,
    error: str | None = None,
) -> None:
    """记录一次任务执行结果（供后台「执行情况」展示）。status: ok|error|skipped。"""
    from app.core.response import now_local

    jid = str(job_id or "").strip()
    if not jid:
        return
    st = str(status or "ok").strip().lower()
    if st not in ("ok", "error", "skipped"):
        st = "ok"
    dur = max(0, int(duration_ms or 0))
    err = (error or "").strip()[:240] or None
    prev = await get_job_stat(jid) or {}
    ok_count = int(prev.get("ok_count") or 0)
    error_count = int(prev.get("error_count") or 0)
    skip_count = int(prev.get("skip_count") or 0)
    if st == "ok":
        ok_count += 1
    elif st == "error":
        error_count += 1
    else:
        skip_count += 1
    payload = {
        "last_run_at": format_scheduler_wall_time(now_local()),
        "last_status": st,
        "last_duration_ms": dur,
        "last_error": err,
        "ok_count": ok_count,
        "error_count": error_count,
        "skip_count": skip_count,
    }
    _job_stats_mem[jid] = payload
    try:
        from app.core.redis_client import try_get_redis

        r = await try_get_redis()
        if r is not None:
            await r.hset(JOB_STATS_KEY, jid, json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        logger.debug("写入任务执行统计失败 job_id=%s: %s", jid, e)


async def get_job_stat(job_id: str) -> dict[str, Any] | None:
    jid = str(job_id or "").strip()
    if not jid:
        return None
    try:
        from app.core.redis_client import try_get_redis

        r = await try_get_redis()
        if r is not None:
            raw = await r.hget(JOB_STATS_KEY, jid)
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return _job_stats_mem.get(jid)


async def load_all_job_stats() -> dict[str, dict[str, Any]]:
    """读取全部任务最近执行情况（Redis 优先，并合并本进程内存）。"""
    out: dict[str, dict[str, Any]] = dict(_job_stats_mem)
    try:
        from app.core.redis_client import try_get_redis

        r = await try_get_redis()
        if r is not None:
            raw_map = await r.hgetall(JOB_STATS_KEY)
            for jid, raw in (raw_map or {}).items():
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        # 兼容旧版 ISO 字符串
                        if data.get("last_run_at"):
                            data["last_run_at"] = normalize_scheduler_wall_time(data.get("last_run_at"))
                        out[str(jid)] = data
                except Exception:
                    continue
    except Exception as e:
        logger.debug("读取任务执行统计失败: %s", e)
    for jid, data in list(out.items()):
        if isinstance(data, dict) and data.get("last_run_at"):
            data["last_run_at"] = normalize_scheduler_wall_time(data.get("last_run_at"))
    return out


async def save_next_runs(mapping: dict[str, str | None]) -> None:
    """Leader 全量刷新各任务下次执行墙钟到 Redis（覆盖写）。"""
    clean: dict[str, str] = {}
    for jid, wall in (mapping or {}).items():
        w = normalize_scheduler_wall_time(wall) if wall else None
        if jid and w:
            clean[str(jid)] = w
    _next_runs_mem.clear()
    _next_runs_mem.update(clean)
    try:
        from app.core.redis_client import try_get_redis

        r = await try_get_redis()
        if r is None:
            return
        pipe = r.pipeline()
        await pipe.delete(NEXT_RUNS_KEY)
        if clean:
            await pipe.hset(NEXT_RUNS_KEY, mapping=clean)
        await pipe.execute()
    except Exception as e:
        logger.debug("写入下次执行缓存失败: %s", e)


async def upsert_next_run(job_id: str, wall: str | None) -> None:
    """单任务更新下次执行（任务跑完后调用，不覆盖其它任务）。"""
    jid = str(job_id or "").strip()
    if not jid:
        return
    w = normalize_scheduler_wall_time(wall) if wall else None
    if not w:
        _next_runs_mem.pop(jid, None)
        try:
            from app.core.redis_client import try_get_redis

            r = await try_get_redis()
            if r is not None:
                await r.hdel(NEXT_RUNS_KEY, jid)
        except Exception:
            pass
        return
    _next_runs_mem[jid] = w
    try:
        from app.core.redis_client import try_get_redis

        r = await try_get_redis()
        if r is not None:
            await r.hset(NEXT_RUNS_KEY, jid, w)
    except Exception as e:
        logger.debug("更新下次执行失败 job_id=%s: %s", jid, e)


async def load_next_runs() -> dict[str, str]:
    out: dict[str, str] = dict(_next_runs_mem)
    try:
        from app.core.redis_client import try_get_redis

        r = await try_get_redis()
        if r is not None:
            raw_map = await r.hgetall(NEXT_RUNS_KEY)
            for jid, wall in (raw_map or {}).items():
                w = normalize_scheduler_wall_time(wall)
                if w:
                    out[str(jid)] = w
    except Exception as e:
        logger.debug("读取下次执行缓存失败: %s", e)
    return out


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
        return f"每天 {hour:02d}:{minute:02d}（业务时区）"
    if t == "cron_interval":
        every = int(schedule.get("every_minutes") or 1)
        return f"每 {every} 分钟"
    return "—"


def _cron_timezone():
    """cron 一律按业务时区（默认 Asia/Shanghai），避免服务器 UTC 导致凌晨任务错点。"""
    try:
        from app.core.response import _biz_timezone

        return _biz_timezone()
    except Exception:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Shanghai")


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
    tz = _cron_timezone()
    if s["type"] == "cron":
        return CronTrigger(hour=s["hour"], minute=s["minute"], timezone=tz)
    if s["type"] == "cron_interval":
        return CronTrigger(minute=f"*/{s['every_minutes']}", timezone=tz)
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


async def get_overrides_version() -> int:
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        raw = await r.get(OVERRIDES_VER_KEY)
        if raw is None:
            return 0
        return int(raw)
    except Exception:
        return 0


async def notify_overrides_changed(*, job_id: str | None = None) -> int:
    """任何进程保存覆盖后调用：bump 版本并 pub，Leader 订阅后热应用。"""
    global _local_overrides_ver
    ver = 0
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        ver = int(await r.incr(OVERRIDES_VER_KEY))
        await r.expire(OVERRIDES_VER_KEY, 30 * 24 * 3600)
        _local_overrides_ver = ver
        from app.core.response import dumps_json

        payload = dumps_json({"ver": ver, "job_id": job_id})
        await r.publish(OVERRIDES_CHANNEL, payload)
    except Exception:
        logger.exception("通知定时任务覆盖变更失败")
    return ver


async def save_overrides(db: AsyncSession, overrides: dict[str, Any]) -> None:
    from app.core.response import dumps_json

    raw = dumps_json(overrides, compact=True)
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


async def reload_overrides_to_scheduler(scheduler) -> None:
    """Leader：从 DB 重载覆盖并热更新（含 enabled 内存缓存）。"""
    from app.core.database import AsyncSessionLocal
    from app.tasks.scheduler import TASK_CATALOG

    if scheduler is None or not getattr(scheduler, "running", False):
        return
    async with AsyncSessionLocal() as db:
        overrides = await load_overrides(db)
    apply_all_overrides(scheduler, TASK_CATALOG, overrides)
    global _local_overrides_ver
    _local_overrides_ver = await get_overrides_version()
    logger.info("已热应用定时任务覆盖 ver=%s jobs=%s", _local_overrides_ver, len(overrides))


async def start_overrides_watcher(scheduler) -> None:
    """Leader 进程订阅覆盖变更；兼轮询版本以防 pub/sub 丢消息。"""
    global _overrides_watch_task, _local_overrides_ver

    await stop_overrides_watcher()
    _local_overrides_ver = await get_overrides_version()

    async def _run() -> None:
        global _local_overrides_ver
        while True:
            try:
                from app.core.redis_client import get_redis

                r = await get_redis()
                pubsub = r.pubsub()
                await pubsub.subscribe(OVERRIDES_CHANNEL)
                logger.info("scheduler overrides subscriber started")
                # 用 get_message 轮询，顺便做版本兜底
                while True:
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg and msg.get("type") == "message":
                        try:
                            await reload_overrides_to_scheduler(scheduler)
                        except Exception:
                            logger.exception("订阅触发重载定时任务覆盖失败")
                    else:
                        # 兜底：对比版本号（其他进程已 bump 但 pub 丢失）
                        try:
                            ver = await get_overrides_version()
                            if ver > _local_overrides_ver:
                                await reload_overrides_to_scheduler(scheduler)
                        except Exception:
                            logger.exception("轮询定时任务覆盖版本失败")
                    await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("scheduler overrides subscriber 断开，3s 后重连: %s", e)
                await asyncio.sleep(3)

    _overrides_watch_task = asyncio.create_task(_run())


async def stop_overrides_watcher() -> None:
    global _overrides_watch_task
    if _overrides_watch_task is not None:
        _overrides_watch_task.cancel()
        try:
            await _overrides_watch_task
        except (asyncio.CancelledError, Exception):
            pass
        _overrides_watch_task = None


@asynccontextmanager
async def redis_job_lock(job_id: str, *, ttl: int = 180) -> AsyncIterator[bool]:
    """跨进程任务互斥：双 Leader 短窗口下仍避免 Token/开关店等非 claim 任务双跑。

    yield True 表示拿到锁；False 表示已被占用，调用方应跳过。
    """
    from app.core.redis_client import get_redis

    key = f"snh:scheduler:joblock:{job_id}"
    token = uuid.uuid4().hex
    acquired = False
    try:
        r = await get_redis()
        acquired = bool(await r.set(key, token, nx=True, ex=max(5, int(ttl))))
    except Exception as e:
        logger.warning("任务锁获取失败 job_id=%s: %s（fail-open 继续执行）", job_id, e)
        # Redis 不可用时 fail-open：依赖 Leader fence，避免调度全停
        yield True
        return
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        try:
            r = await get_redis()
            await r.eval(_LUA_COMPARE_DELETE, 1, key, token)
        except Exception:
            logger.warning("任务锁释放失败 job_id=%s", job_id)

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
from app.core.cache_keys import (
    SCHEDULER_JOB_STATS_KEY,
    SCHEDULER_NEXT_RUNS_KEY,
    SCHEDULER_OVERRIDES_CHANNEL,
    SCHEDULER_OVERRIDES_VER_KEY,
    scheduler_job_lock_key,
)

OVERRIDES_VER_KEY = SCHEDULER_OVERRIDES_VER_KEY
OVERRIDES_CHANNEL = SCHEDULER_OVERRIDES_CHANNEL
JOB_STATS_KEY = SCHEDULER_JOB_STATS_KEY
NEXT_RUNS_KEY = SCHEDULER_NEXT_RUNS_KEY

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

# compare-and-expire：只有仍持有当前 Token 的任务才能续租。
_LUA_COMPARE_EXPIRE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""

# Lua：原子更新任务执行统计，避免 HGET→改计数→HSET 的丢更新
_LUA_RECORD_JOB_RUN = """
local prev = redis.call('hget', KEYS[1], ARGV[1])
local ok_c, err_c, skip_c = 0, 0, 0
if prev then
  local ok, obj = pcall(cjson.decode, prev)
  if ok and type(obj) == 'table' then
    ok_c = tonumber(obj['ok_count'] or 0) or 0
    err_c = tonumber(obj['error_count'] or 0) or 0
    skip_c = tonumber(obj['skip_count'] or 0) or 0
  end
end
local st = ARGV[2]
if st == 'ok' then
  ok_c = ok_c + 1
elseif st == 'error' then
  err_c = err_c + 1
else
  skip_c = skip_c + 1
end
local payload = cjson.encode({
  last_run_at = ARGV[3],
  last_status = st,
  last_duration_ms = tonumber(ARGV[4]) or 0,
  last_error = (ARGV[5] ~= '' and ARGV[5] or nil),
  ok_count = ok_c,
  error_count = err_c,
  skip_count = skip_c
})
redis.call('hset', KEYS[1], ARGV[1], payload)
return payload
"""

# 内存缓存：供 _leader_job 快速判断 enabled（与 DB 覆盖同步）
_enabled_cache: dict[str, bool] = {}
_overrides_watch_task: asyncio.Task | None = None
_local_overrides_ver: int = 0
# 同进程内串行化同 job 的统计写入（跨 await 防交错）
_job_stats_locks: dict[str, asyncio.Lock] = {}

# Leader 丢失时主动取消当前进程内已启动的定时任务。
# APScheduler.shutdown(wait=False) 只停止调度，不保证取消已经运行的 coroutine。
_active_leader_job_tasks: set[asyncio.Task] = set()


def active_leader_job_count() -> int:
    """当前尚未退出的 Leader 定时任务数；用于降级时决定是否允许本进程重新竞选。"""
    return sum(1 for task in tuple(_active_leader_job_tasks) if not task.done())


async def cancel_active_leader_jobs(*, timeout: float = 5.0) -> int:
    """取消并等待本进程正在运行的 Leader 定时任务，给事务清理留出有限时间。"""
    current = asyncio.current_task()
    tasks = [
        task for task in tuple(_active_leader_job_tasks)
        if task is not current and not task.done()
    ]
    for task in tasks:
        task.cancel()
    if not tasks:
        return 0
    done, pending = await asyncio.wait(
        tasks, timeout=max(0.1, float(timeout))
    )
    if pending:
        logger.error(
            "等待 Leader 定时任务取消超时 count=%s pending=%s；可能存在屏蔽取消的外部 I/O",
            len(tasks),
            len(pending),
        )
    return len(tasks)



class JobSkipped(Exception):
    """任务主动跳过（如跨进程锁未拿到）。_leader_job 记为 skipped，勿记 ok。"""

    def __init__(self, reason: str = "skipped") -> None:
        self.reason = (reason or "skipped").strip()[:240] or "skipped"
        super().__init__(self.reason)


# MySQL / 驱动：本地重导 struct、MySQL 重启、连接被掐时常见；记 skipped 勿刷 error_count
_INFRA_MYSQL_CODES = frozenset(
    {
        1049,  # Unknown database
        1146,  # Table doesn't exist
        2002,  # Can't connect via socket
        2003,  # Can't connect to MySQL server
        2006,  # MySQL server has gone away
        2013,  # Lost connection during query
        2014,  # Commands out of sync
        1205,  # Lock wait timeout exceeded
        1213,  # Deadlock found
    }
)
_INFRA_MSG_HINTS = (
    "unknown database",
    "doesn't exist",
    "server has gone away",
    "lost connection",
    "can't connect",
    "connection refused",
    "too many connections",
    "not connected",
    "connection reset",
    "timed out",
    "timeout",
)


def _walk_exception_chain(exc: BaseException) -> list[BaseException]:
    seen: set[int] = set()
    out: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(cur)
        nxt = getattr(cur, "orig", None)
        if isinstance(nxt, BaseException) and id(nxt) not in seen:
            cur = nxt
            continue
        cur = cur.__cause__ or cur.__context__
    return out


def _mysql_errno(exc: BaseException) -> int | None:
    args = getattr(exc, "args", None)
    if not args:
        return None
    try:
        code = int(args[0])
    except (TypeError, ValueError):
        return None
    return code if code > 0 else None


def infra_skip_reason(exc: BaseException) -> str | None:
    """基础设施瞬态错误 → 跳过原因；业务/未知异常返回 None（仍记 error）。

    覆盖：库/表重建间隙、MySQL 宕机或断连、锁等待/死锁、常见网络超时。
    """
    if isinstance(exc, asyncio.CancelledError):
        return "shutdown_cancelled"
    if isinstance(exc, JobSkipped):
        return exc.reason

    try:
        from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, ProgrammingError
    except Exception:  # pragma: no cover
        DBAPIError = InterfaceError = OperationalError = ProgrammingError = ()  # type: ignore

    chain = _walk_exception_chain(exc)
    # 优先认 MySQL errno（在 orig / 驱动异常上），避免被外层 OperationalError 文案抢先
    for node in chain:
        code = _mysql_errno(node)
        if code in _INFRA_MYSQL_CODES:
            return f"db_infra:{code}"

    for node in chain:
        msg = str(node).lower()
        if any(h in msg for h in _INFRA_MSG_HINTS):
            # 收紧：仅对 DBAPI / 连接类异常用文案匹配，避免误伤业务 ValueError
            if isinstance(
                node,
                (
                    OperationalError,
                    ProgrammingError,
                    InterfaceError,
                    DBAPIError,
                    ConnectionError,
                    TimeoutError,
                    OSError,
                ),
            ) or type(node).__module__.startswith(("pymysql", "aiomysql", "asyncmy")):
                return f"db_infra:{type(node).__name__}"
        if isinstance(node, (TimeoutError, ConnectionError)):
            return f"net_infra:{type(node).__name__}"
    return None


def is_retryable_infra(exc: BaseException) -> bool:
    """断连类可短重试一次；表/库不存在重试无意义。"""
    codes = [_mysql_errno(n) for n in _walk_exception_chain(exc)]
    codes = [c for c in codes if c is not None]
    if any(c in (1049, 1146) for c in codes):
        return False
    if any(c in (2002, 2003, 2006, 2013, 2014, 1205, 1213) for c in codes):
        return True
    for node in _walk_exception_chain(exc):
        name = type(node).__name__.lower()
        if "timeout" in name or "disconnect" in name or "connection" in name:
            return True
    return False


def leader_job(fn):
    """定时任务全局包装：Leader fence + 启停门闩 + 执行统计 + 基础设施容错。

    - JobSkipped / 库表重建 / MySQL 断连：记 skipped，不抛给 APScheduler（避免 ERROR 刷屏）
    - CancelledError：记 skipped 后原样抛出（保证关机可中断）
    - 其它异常：记 error 后抛出
    - 基础设施错误：记录 skipped，等待下一次调度，不对结果不确定的任务自动重放
    """

    async def _wrapped(*args, **kwargs):
        import time

        from app.core.leader_lock import confirm_leader_owned, is_leader_held
        from app.core.leader_fencing import active_leader_epoch, leader_fence_scope

        if not is_leader_held():
            return None
        if not await confirm_leader_owned():
            return None
        if active_leader_epoch() is None:
            logger.critical(
                "Leader job 拒绝执行：数据库 fencing epoch 未激活 job=%s",
                getattr(fn, "__name__", "unknown"),
            )
            return None
        job_name = getattr(fn, "__name__", "")
        if job_name and not is_job_enabled(job_name):
            return None
        started = time.perf_counter()
        current_task = asyncio.current_task()
        if current_task is not None:
            _active_leader_job_tasks.add(current_task)

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

        async def _finish(status: str, error: str | None = None) -> None:
            if not job_name:
                return
            # 任务统计/下次运行时间属于观测数据，不能覆盖业务任务的真实结果，
            # 尤其不能在 CancelledError 路径中因统计库故障吞掉取消信号。
            try:
                await record_job_run(
                    job_name,
                    status=status,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error=error,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("记录定时任务执行统计失败 job=%s status=%s", job_name, status)
            try:
                await _refresh_next_run()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("刷新定时任务下次执行时间失败 job=%s", job_name, exc_info=True)

        async def _invoke():
            # ContextVar 会传播到本任务创建的 asyncio 子任务；每个 DB commit
            # 都会在 SQLAlchemy before_commit 中验证 epoch 并锁住 fencing 行。
            with leader_fence_scope():
                return await fn(*args, **kwargs)

        try:
            try:
                result = await _invoke()
            except asyncio.CancelledError:
                await _finish("skipped", "shutdown_cancelled")
                raise
            except JobSkipped as skip:
                await _finish("skipped", skip.reason)
                return None
            except Exception as first:
                reason = infra_skip_reason(first)
                if reason:
                    # 连接中断可能发生在 DB COMMIT 已成功、客户端尚未收到确认之后。
                    # 自动重放整个 job 会重复发券/推送/对账；交由下一次调度和行级幂等恢复。
                    logger.warning(
                        "定时任务基础设施暂不可用，跳过本次且不自动重放 job=%s: %s",
                        job_name,
                        reason,
                    )
                    await _finish("skipped", reason)
                    return None
                await _finish("error", str(first) or type(first).__name__)
                raise
            await _finish("ok")
            return result
        except asyncio.CancelledError:
            raise
        finally:
            if current_task is not None:
                _active_leader_job_tasks.discard(current_task)

    _wrapped.__name__ = getattr(fn, "__name__", "leader_job")
    _wrapped.__qualname__ = getattr(fn, "__qualname__", _wrapped.__name__)
    return _wrapped


def _job_stats_lock(job_id: str) -> asyncio.Lock:
    # setdefault 避免 get→create→assign 竞态下同 job 多把锁
    return _job_stats_locks.setdefault(job_id, asyncio.Lock())


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
    """记录一次任务执行结果（供后台「执行情况」展示）。status: ok|error|skipped。

    Redis 用 Lua 原子递增计数；同进程再用 asyncio.Lock 串行化，避免跨 await 交错。
    """
    from app.core.response import now_local

    jid = str(job_id or "").strip()
    if not jid:
        return
    st = str(status or "ok").strip().lower()
    if st not in ("ok", "error", "skipped"):
        st = "ok"
    dur = max(0, int(duration_ms or 0))
    err = (error or "").strip()[:240] or None
    wall = format_scheduler_wall_time(now_local()) or ""

    async with _job_stats_lock(jid):
        # 优先 Redis Lua（跨进程原子）
        try:
            from app.core.redis_client import try_get_redis

            r = await try_get_redis()
            if r is not None:
                raw = await r.eval(
                    _LUA_RECORD_JOB_RUN,
                    1,
                    JOB_STATS_KEY,
                    jid,
                    st,
                    wall,
                    str(dur),
                    err or "",
                )
                if raw:
                    data = json.loads(raw if isinstance(raw, str) else raw.decode())
                    if isinstance(data, dict):
                        # cjson 可能把 null 编成缺失；统一字段
                        data["last_error"] = data.get("last_error") or None
                        if not data.get("last_error"):
                            data["last_error"] = None
                        _job_stats_mem[jid] = data
                        return
        except Exception as e:
            logger.debug("原子写入任务统计失败，降级内存 job_id=%s: %s", jid, e)

        prev = _job_stats_mem.get(jid) or {}
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
            "last_run_at": wall,
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
    """Leader 全量刷新各任务下次执行墙钟到 Redis。

    不用 DELETE 再 HSET（中间窗口读空）；改为 HSET 全量后删除多余 field。
    """
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
        existing = await r.hkeys(NEXT_RUNS_KEY)
        pipe = r.pipeline()
        if clean:
            pipe.hset(NEXT_RUNS_KEY, mapping=clean)
        stale = [k for k in (existing or []) if str(k) not in clean]
        if stale:
            pipe.hdel(NEXT_RUNS_KEY, *stale)
        if not clean and existing:
            pipe.delete(NEXT_RUNS_KEY)
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

    key = scheduler_job_lock_key(job_id)
    token = uuid.uuid4().hex
    lease_ttl = max(5, int(ttl))
    acquired = False
    try:
        r = await get_redis()
        acquired = bool(await r.set(key, token, nx=True, ex=lease_ttl))
    except Exception as e:
        # 任务锁是防止重复执行的最后一道跨进程保护；Redis 不可用时必须 fail-closed。
        # 继续执行可能在 Leader 租约短暂分歧/网络分区时造成双重发券、重复对账或重复同步。
        logger.error("任务锁获取失败 job_id=%s: %s（fail-closed 跳过本次任务）", job_id, e)
        yield False
        return
    if not acquired:
        yield False
        return

    owner_task = asyncio.current_task()
    stop_renew = asyncio.Event()

    async def _renew_lease() -> None:
        # 任务可长于初始 TTL；续租失败即取消持锁任务，不能让旧执行者继续产生副作用。
        interval = max(1.0, min(30.0, lease_ttl / 3.0))
        while not stop_renew.is_set():
            try:
                await asyncio.wait_for(stop_renew.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            try:
                renewed = await r.eval(
                    _LUA_COMPARE_EXPIRE, 1, key, token, lease_ttl
                )
            except Exception as e:
                logger.error("任务锁续租异常 job_id=%s: %s，取消任务", job_id, e)
                if owner_task is not None:
                    owner_task.cancel()
                return
            if not renewed:
                logger.error("任务锁已丢失 job_id=%s，取消旧持有者任务", job_id)
                if owner_task is not None:
                    owner_task.cancel()
                return

    renew_task = asyncio.create_task(_renew_lease())
    try:
        yield True
    finally:
        stop_renew.set()
        renew_task.cancel()
        try:
            await renew_task
        except asyncio.CancelledError:
            pass
        try:
            await r.eval(_LUA_COMPARE_DELETE, 1, key, token)
        except Exception:
            logger.warning("任务锁释放失败 job_id=%s", job_id)


@asynccontextmanager
async def exclusive_scheduler_job(job_id: str, *, ttl: int = 180) -> AsyncIterator[None]:
    """拿到跨进程任务锁后执行；拿不到则抛 JobSkipped（供 _leader_job 记 skipped）。"""
    async with redis_job_lock(job_id, ttl=ttl) as ok:
        if not ok:
            raise JobSkipped("job_lock_busy")
        yield

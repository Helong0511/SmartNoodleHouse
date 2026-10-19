"""定时任务启停/改周期：覆盖项校验与审计 target_id 约束。"""
from __future__ import annotations

import pytest

from app.services import scheduler_admin as sa
from app.services.scheduler_admin import validate_schedule


def test_validate_schedule_interval():
    assert validate_schedule({"type": "interval", "unit": "minutes", "every": 4}) == {
        "type": "interval",
        "unit": "minutes",
        "every": 4,
    }


def test_validate_schedule_rejects_bad_unit():
    with pytest.raises(ValueError, match="unit"):
        validate_schedule({"type": "interval", "unit": "days", "every": 1})


def test_validate_schedule_cron_interval():
    assert validate_schedule({"type": "cron_interval", "every_minutes": 2}) == {
        "type": "cron_interval",
        "every_minutes": 2,
    }


def test_scheduler_audit_must_not_use_string_target_id():
    """regression: operation_logs.target_id 是 bigint，字符串 job_id 会 DataError 500。"""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "backend" / "app" / "api" / "settings.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # 找 update_scheduler_job 内对 _write_audit 的调用
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "_write_audit":
            continue
        # 确认在 update_scheduler_job 作用域附近：检查 kwargs
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        if "target_type" not in kwargs:
            continue
        tt = kwargs["target_type"]
        if isinstance(tt, ast.Constant) and tt.value == "scheduler_job":
            found = True
            tid = kwargs.get("target_id")
            assert isinstance(tid, ast.Constant) and tid.value is None, (
                "scheduler 审计 target_id 必须为 None（job_id 放 detail）"
            )
    assert found, "未找到 scheduler_job 审计写入"


@pytest.mark.asyncio
async def test_record_job_run_memory_fallback(monkeypatch):
    """Redis 不可用时仍应写入进程内统计，供后台展示。"""
    sa._job_stats_mem.clear()

    async def _no_redis():
        return None

    monkeypatch.setattr("app.core.redis_client.try_get_redis", _no_redis)
    await sa.record_job_run("drain_webhook_inbox", status="ok", duration_ms=12)
    await sa.record_job_run("drain_webhook_inbox", status="error", duration_ms=30, error="boom")
    st = await sa.get_job_stat("drain_webhook_inbox")
    assert st is not None
    assert st["last_status"] == "error"
    assert st["last_error"] == "boom"
    assert st["ok_count"] == 1
    assert st["error_count"] == 1
    assert st["last_duration_ms"] == 30
    # 墙钟格式，无 T / 无时区偏移
    assert st["last_run_at"] and "T" not in st["last_run_at"] and "+" not in st["last_run_at"]
    all_stats = await sa.load_all_job_stats()
    assert "drain_webhook_inbox" in all_stats


def test_normalize_scheduler_wall_time_timezone():
    """UTC ISO 须换算成业务时区墙钟，不能直接砍掉 +00:00。"""
    # 2026-10-19 06:00:00 UTC = 14:00:00 Asia/Shanghai
    wall = sa.normalize_scheduler_wall_time("2026-10-19T06:00:00+00:00")
    assert wall == "2026-10-19 14:00:00"
    assert sa.normalize_scheduler_wall_time("2026-10-19 14:00:00") == "2026-10-19 14:00:00"
    assert sa.format_scheduler_wall_time(None) is None

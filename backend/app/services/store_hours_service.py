"""门店营业时间服务与自动开/关店判定。

模型：每个 (store_id, weekday, slot_index) 一行，close_time<=open_time 视为跨午夜。
当天没有任何 is_open 时段即当日休息；门店完全没有配置时不参与自动开关。
"""
from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any, Iterable

from sqlalchemy import delete, select

from app.core.response import naive_local_now
from app.models import PlatformStoreBinding, StoreBusinessHours

logger = logging.getLogger(__name__)

WEEKDAY_MIN, WEEKDAY_MAX = 1, 7


def _parse_time(value: str) -> time:
    value = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"时间格式应为 HH:MM: {value}")


def _hm(t: time) -> str:
    return f"{t.hour}:{t.minute:02d}"


def _python_weekday(at: datetime) -> int:
    """Python weekday()（周一=0）转为本模型 weekday（周一=1 ... 周日=7）。"""
    return at.weekday() + 1


def is_open_at(rows: Iterable[Any], at: datetime) -> bool:
    """判定 at 时刻是否处于营业时段。rows 为 StoreBusinessHours 对象列表。

    支持多时段与跨午夜：当天 22:00-02:00 既覆盖当天 22:00 后，也覆盖次日 02:00 前。
    边界：到达 open_time 即营业，到达 close_time 即休息。
    """
    wd = _python_weekday(at)
    t = at.time()
    yesterday = (wd - 2) % 7 + 1
    for r in rows:
        if not getattr(r, "is_open", True):
            continue
        o, c = r.open_time, r.close_time
        cross = bool(getattr(r, "cross_day", False)) or c <= o
        if r.weekday == wd:
            if cross:
                if t >= o:
                    return True
            elif o <= t < c:
                return True
        if cross and r.weekday == yesterday and t < c:
            return True
    return False


def desired_open_status(rows: list[Any], at: datetime) -> str | None:
    """返回期望状态 OPEN / REST；门店完全没有配置时返回 None（不自动控制）。"""
    if not rows:
        return None
    return "OPEN" if is_open_at(rows, at) else "REST"


def shipping_time_for_day(rows: list[Any], weekday: int) -> str:
    """生成美团 shipping_time 风格串（如 7:00-9:00,11:30-19:00），无时段返回空串。"""
    slots = sorted(
        (r for r in rows if r.is_open and r.weekday == weekday),
        key=lambda r: (r.slot_index, r.open_time),
    )
    return ",".join(f"{_hm(r.open_time)}-{_hm(r.close_time)}" for r in slots)


def serialize_hours(rows: list[StoreBusinessHours]) -> list[dict[str, Any]]:
    """按 1~7 补齐成 API 结构。"""
    grouped: dict[int, list[StoreBusinessHours]] = {}
    for r in rows:
        grouped.setdefault(r.weekday, []).append(r)
    result = []
    for wd in range(WEEKDAY_MIN, WEEKDAY_MAX + 1):
        slots = sorted(grouped.get(wd, []), key=lambda r: r.slot_index)
        result.append(
            {
                "weekday": wd,
                "is_open": bool(slots),
                "slots": [
                    {
                        "open_time": _hm(r.open_time),
                        "close_time": _hm(r.close_time),
                        "cross_day": bool(r.cross_day or r.close_time <= r.open_time),
                    }
                    for r in slots
                ],
            }
        )
    return result


class StoreHoursService:
    @staticmethod
    async def list_hours(db, store_id: int) -> list[StoreBusinessHours]:
        rows = (
            await db.execute(
                select(StoreBusinessHours)
                .where(StoreBusinessHours.store_id == store_id)
                .order_by(StoreBusinessHours.weekday, StoreBusinessHours.slot_index)
            )
        ).scalars().all()
        return list(rows)

    @staticmethod
    async def get_hours_payload(db, store_id: int) -> list[dict[str, Any]]:
        return serialize_hours(await StoreHoursService.list_hours(db, store_id))

    @staticmethod
    async def set_hours(db, store_id: int, days: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """全量覆盖某门店营业时间。

        days: [{"weekday":1, "is_open":true, "slots":[{"open_time":"07:00","close_time":"09:00"}]}]
        is_open=false 或缺 slots 表示该天整体休息（不落库任何行）。
        """
        normalized: list[tuple[int, int, time, time, bool]] = []
        for d in days:
            wd = int(d["weekday"])
            if not (WEEKDAY_MIN <= wd <= WEEKDAY_MAX):
                raise ValueError(f"weekday 取值需在 1~7: {wd}")
            if not d.get("is_open", True):
                continue
            for idx, s in enumerate(d.get("slots", [])):
                o = _parse_time(s["open_time"])
                c = _parse_time(s["close_time"])
                cross = bool(s.get("cross_day", False)) or c <= o
                normalized.append((wd, idx, o, c, cross))
        await db.execute(delete(StoreBusinessHours).where(StoreBusinessHours.store_id == store_id))
        for wd, idx, o, c, cross in normalized:
            db.add(
                StoreBusinessHours(
                    store_id=store_id,
                    weekday=wd,
                    slot_index=idx,
                    open_time=o,
                    close_time=c,
                    cross_day=cross,
                    is_open=True,
                )
            )
        await db.commit()
        return await StoreHoursService.get_hours_payload(db, store_id)

    @staticmethod
    async def apply_auto_open_tick(db) -> dict[str, int]:
        """每分钟执行：仅处理 auto_sync_open=true 的有效绑定，按营业时间切换平台营业状态。

        返回 {"opened": n, "closed": n, "skipped": n}。
        逐绑定独立事务 + FOR UPDATE SKIP LOCKED，避免单店失败回滚整批、双进程互踩。
        """
        from app.core.database import AsyncSessionLocal
        from app.services.delivery_context import get_credentials_for_store
        from app.services.delivery_providers import get_delivery_provider

        now = naive_local_now()
        stats = {"opened": 0, "closed": 0, "skipped": 0}
        binding_ids = list(
            (
                await db.execute(
                    select(PlatformStoreBinding.id).where(
                        PlatformStoreBinding.status == 1,
                        PlatformStoreBinding.auto_sync_open.is_(True),
                    )
                )
            ).scalars().all()
        )

        for bid in binding_ids:
            try:
                # 1) 短锁：算目标状态、取凭证后立刻释放，避免持绑定行锁调平台 API
                async with AsyncSessionLocal() as sdb:
                    binding = (
                        await sdb.execute(
                            select(PlatformStoreBinding)
                            .where(PlatformStoreBinding.id == int(bid))
                            .with_for_update(skip_locked=True)
                        )
                    ).scalar_one_or_none()
                    if not binding:
                        stats["skipped"] += 1
                        continue
                    rows = await StoreHoursService.list_hours(sdb, binding.store_id)
                    desired = desired_open_status(rows, now)
                    if desired is None or binding.platform_open_status == desired:
                        stats["skipped"] += 1
                        await sdb.commit()
                        continue
                    shipping = (
                        shipping_time_for_day(rows, _python_weekday(now)) if desired == "REST" else None
                    )
                    creds = await get_credentials_for_store(sdb, binding.platform, binding.store_id)
                    platform = binding.platform
                    store_id = int(binding.store_id)
                    await sdb.commit()

                # 2) 平台 API：无 DB 行锁
                provider = get_delivery_provider(platform, creds)
                ok = await provider.set_store_open_status(desired, shipping)
                if not ok:
                    logger.warning(
                        "自动开关店失败 store=%s platform=%s desired=%s",
                        store_id,
                        platform,
                        desired,
                    )
                    stats["skipped"] += 1
                    continue

                # 3) 短锁写回状态
                async with AsyncSessionLocal() as sdb:
                    binding = (
                        await sdb.execute(
                            select(PlatformStoreBinding)
                            .where(PlatformStoreBinding.id == int(bid))
                            .with_for_update(skip_locked=True)
                        )
                    ).scalar_one_or_none()
                    if not binding:
                        stats["skipped"] += 1
                        continue
                    if binding.platform_open_status == desired:
                        stats["skipped"] += 1
                        await sdb.commit()
                        continue
                    binding.platform_open_status = desired
                    binding.last_sync_at = now
                    await sdb.commit()
                    stats["opened" if desired == "OPEN" else "closed"] += 1
            except Exception:  # noqa: BLE001 单店失败不影响其它门店
                logger.exception("自动开关店异常 binding_id=%s", bid)
                stats["skipped"] += 1
        return stats

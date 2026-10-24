"""迎宾链路诊断：开关 / 摄像头 / 队列 / 最近访客与迎宾日志。"""
from __future__ import annotations

import asyncio

from sqlalchemy import desc, select, text

from app.core.cache_keys import SYSTEM_SETTINGS_VER_KEY, TTS_WELCOME_QUEUE_KEY
from app.core.database import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.models import Camera, SystemSetting, VisitorEvent, WelcomeLog
from app.services.system_settings_service import SystemSettingsService


async def main() -> None:
    r = await get_redis()
    print("queue_len", await r.llen(TTS_WELCOME_QUEUE_KEY))
    print("settings_ver", await r.get(SYSTEM_SETTINGS_VER_KEY))

    async with AsyncSessionLocal() as db:
        await SystemSettingsService.invalidate_cache()
        await SystemSettingsService._load_raw(db, force=True)
        keys = [
            "camera_speaker_tts_enabled",
            "unknown_visitor_welcome_enabled",
            "unknown_visitor_welcome_text",
            "unknown_visitor_welcome_cooldown_seconds",
            "unknown_visitor_capture_enabled",
            "welcome_cooldown_seconds",
            "face_match_threshold",
            "face_confirm_frames",
        ]
        for k in keys:
            row = (
                await db.execute(select(SystemSetting).where(SystemSetting.setting_key == k))
            ).scalar_one_or_none()
            print(f"db[{k}]={(None if row is None else row.setting_value)!r}")

        print(
            "eff camera_speaker",
            await SystemSettingsService.effective_bool(
                db, "camera_speaker_tts_enabled", False, store_id=None
            ),
        )
        print(
            "eff unknown_welcome",
            await SystemSettingsService.effective_bool(
                db, "unknown_visitor_welcome_enabled", True, store_id=None
            ),
        )

        cams = (await db.execute(select(Camera))).scalars().all()
        for c in cams:
            sid = int(c.store_id) if c.store_id else None
            en = await SystemSettingsService.effective_bool(
                db, "camera_speaker_tts_enabled", False, store_id=sid
            )
            url = (c.source_url or "")[:48]
            print(
                f"cam id={c.id} status={c.status} store={sid} speaker={en} url={url!r}"
            )

        try:
            ov = (
                await db.execute(
                    text(
                        "SELECT store_id, setting_key, setting_value FROM store_settings "
                        "WHERE setting_key='camera_speaker_tts_enabled'"
                    )
                )
            ).all()
            print("store_overrides", list(ov))
        except Exception as e:
            print("store_overrides_err", type(e).__name__, e)

        events = (
            await db.execute(select(VisitorEvent).order_by(desc(VisitorEvent.id)).limit(8))
        ).scalars().all()
        print("recent_visitor_events", len(events))
        for e in events:
            print(
                f"  ve id={e.id} cam={e.camera_id} type={e.event_type} "
                f"cust={e.customer_id} no={e.visitor_no} t={e.event_time}"
            )

        logs = (
            await db.execute(select(WelcomeLog).order_by(desc(WelcomeLog.id)).limit(8))
        ).scalars().all()
        print("recent_welcome_logs", len(logs))
        for w in logs:
            print(
                f"  wl id={w.id} cust={w.customer_id} cam={w.camera_id} "
                f"t={w.created_at} text={(w.welcome_text or '')[:40]!r}"
            )


if __name__ == "__main__":
    asyncio.run(main())

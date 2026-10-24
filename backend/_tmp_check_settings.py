import asyncio
from sqlalchemy import text
from app.core.database import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT setting_key, setting_value FROM system_settings "
                    "WHERE setting_key IN ("
                    "'camera_speaker_tts_enabled',"
                    "'camera_speaker_volume_percent',"
                    "'unknown_visitor_welcome_enabled',"
                    "'unknown_visitor_welcome_text',"
                    "'welcome_greeting_prefix',"
                    "'welcome_cooldown_seconds'"
                    ")"
                )
            )
        ).all()
        for k, v in rows:
            print(f"{k}={v}")


if __name__ == "__main__":
    asyncio.run(main())

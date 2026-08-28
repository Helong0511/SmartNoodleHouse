from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.biz_no import (  # noqa: F401 — 兼容旧 import 路径
    BIZ_CU,
    BIZ_OD,
    BIZ_OT,
    BIZ_PA,
    BIZ_PW,
    BIZ_RA,
    BIZ_RW,
    BIZ_VI,
    TABLE_DELIVERY,
    TABLE_NONE,
    TABLE_TAKEAWAY,
    generate_no,
    payment_biz,
    refund_biz,
    resolve_table_parts,
    store_code,
    table_code,
)
from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.app_debug,
    pool_pre_ping=True,
    pool_recycle=3600,
)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

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

# 连接池按 gunicorn worker 估算：每 worker 约 pool_size+max_overflow；
# 总连接 ≈ workers × (pool_size + max_overflow)，须小于 MySQL max_connections。
_pool_size = max(5, int(getattr(settings, "db_pool_size", 5) or 5))
_max_overflow = max(0, int(getattr(settings, "db_max_overflow", 10) or 10))

# 固定可重复读：避免会话落到 READ UNCOMMITTED（脏读）。
# 幻读靠行锁 / 间隙锁（SELECT FOR UPDATE）和唯一约束，不靠快照读判断“是否已存在”。
engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    isolation_level="REPEATABLE READ",
)

# expire_on_commit=False：commit 后已加载字段可继续读（订单通知等依赖此行为）。
# 注意：server_default / onupdate 时间戳在 flush 后常未加载，sync 序列化前须 await refresh。
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

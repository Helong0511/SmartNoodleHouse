import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import SystemSetting, VisitorEvent

logger = logging.getLogger(__name__)

# 可在后台在线修改的业务参数（持久化到 system_settings 表，运行时优先于 .env 默认值）
BUSINESS_SETTING_META: dict[str, dict[str, Any]] = {
    "order_payment_timeout_minutes": {
        "label": "订单支付超时",
        "unit": "分钟",
        "type": "int",
        "min": 1,
        "max": 1440,
        "description": "未支付订单自动关闭时间",
    },
    "visitor_data_retention_days": {
        "label": "访客数据保留",
        "unit": "天",
        "type": "int",
        "min": 1,
        "max": 365,
        "description": "匿名访客事件保留天数；缩短时会同步压缩已有记录的过期时间",
    },
    "operation_log_retention_days": {
        "label": "操作日志保留",
        "unit": "天",
        "type": "int",
        "min": 7,
        "max": 3650,
        "description": "后台操作日志自动清理周期",
    },
    "welcome_cooldown_seconds": {
        "label": "迎宾冷却时间",
        "unit": "秒",
        "type": "int",
        "min": 0,
        "max": 3600,
        "description": "同一顾客两次迎宾的最小间隔",
    },
    "face_match_threshold": {
        "label": "人脸匹配阈值",
        "unit": "",
        "type": "float",
        "min": 0.5,
        "max": 0.99,
        "step": 0.01,
        "description": "相似度达到该值才视为同一人（0.75 推荐）",
    },
    "face_confirm_frames": {
        "label": "人脸确认帧数",
        "unit": "帧",
        "type": "int",
        "min": 1,
        "max": 30,
        "description": "连续 N 帧匹配成功才触发迎宾",
    },
}

REDIS_CACHE_VER_KEY = "snh:system_settings:ver"


class SystemSettingsService:
    _cache: dict[str, str] | None = None
    _cache_at: float = 0.0
    _cache_ver: int = -1
    CACHE_TTL = 15.0
    _seed_lock = asyncio.Lock()

    @classmethod
    def _env_default(cls, key: str, settings: Settings | None = None) -> str:
        s = settings or get_settings()
        val = getattr(s, key, None)
        return str(val) if val is not None else ""

    @classmethod
    async def _remote_version(cls) -> int:
        try:
            from app.core.redis_client import get_redis

            r = await get_redis()
            raw = await r.get(REDIS_CACHE_VER_KEY)
            return int(raw or 0)
        except Exception:
            return 0

    @classmethod
    async def _bump_remote_version(cls) -> int:
        try:
            from app.core.redis_client import get_redis

            r = await get_redis()
            return int(await r.incr(REDIS_CACHE_VER_KEY))
        except Exception:
            return 0

    @classmethod
    async def _load_raw(cls, db: AsyncSession, *, force: bool = False) -> dict[str, str]:
        now = time.monotonic()
        remote_ver = await cls._remote_version()
        if (
            not force
            and cls._cache is not None
            and cls._cache_ver == remote_ver
            and now - cls._cache_at < cls.CACHE_TTL
        ):
            return cls._cache
        result = await db.execute(select(SystemSetting))
        cls._cache = {row.setting_key: row.setting_value for row in result.scalars()}
        cls._cache_at = now
        cls._cache_ver = remote_ver
        return cls._cache

    @classmethod
    async def invalidate_cache(cls) -> None:
        """本进程立即失效，并 bump Redis 版本使其他 worker 在下次读取时失效。"""
        cls._cache = None
        cls._cache_at = 0.0
        cls._cache_ver = -1
        await cls._bump_remote_version()

    @classmethod
    def invalidate_cache_local(cls) -> None:
        """仅清本进程（测试或 Redis 不可用时）。"""
        cls._cache = None
        cls._cache_at = 0.0
        cls._cache_ver = -1

    @classmethod
    async def _sqlite_next_id(cls, db: AsyncSession) -> int:
        from sqlalchemy import func

        cur = await db.scalar(select(func.coalesce(func.max(SystemSetting.id), 0)))
        return int(cur or 0) + 1

    @classmethod
    async def _is_sqlite(cls, db: AsyncSession) -> bool:
        bind = db.get_bind()
        return bool(bind and bind.dialect.name == "sqlite")

    @classmethod
    async def ensure_seeded(cls, db: AsyncSession) -> None:
        """首次访问时把 .env 默认值写入 DB；并发下用锁 + savepoint 兜底。"""
        async with cls._seed_lock:
            settings = get_settings()
            result = await db.execute(select(SystemSetting.setting_key))
            existing = set(result.scalars().all())
            added = False
            use_sqlite = await cls._is_sqlite(db)
            next_id = await cls._sqlite_next_id(db) if use_sqlite else None
            for key, meta in BUSINESS_SETTING_META.items():
                if key in existing:
                    continue
                try:
                    async with db.begin_nested():
                        row = SystemSetting(
                            setting_key=key,
                            setting_value=cls._env_default(key, settings),
                            description=meta.get("description"),
                        )
                        if use_sqlite and next_id is not None:
                            row.id = next_id
                            next_id += 1
                        db.add(row)
                        await db.flush()
                    added = True
                except IntegrityError as e:
                    msg = str(getattr(e, "orig", e)).upper()
                    if "UNIQUE" in msg or "DUPLICATE" in msg:
                        logger.debug("system_settings seed race on key=%s", key)
                    else:
                        raise
            if added:
                await cls.invalidate_cache()

    @classmethod
    async def get_value(cls, db: AsyncSession, key: str, fallback: str | int | float) -> str:
        raw = await cls._load_raw(db)
        if key in raw:
            return raw[key]
        return str(fallback)

    @classmethod
    async def get_int(cls, db: AsyncSession, key: str, fallback: int) -> int:
        try:
            return int(await cls.get_value(db, key, fallback))
        except (TypeError, ValueError):
            return fallback

    @classmethod
    async def get_float(cls, db: AsyncSession, key: str, fallback: float) -> float:
        try:
            return float(await cls.get_value(db, key, fallback))
        except (TypeError, ValueError):
            return fallback

    @classmethod
    def _validate_value(cls, key: str, value: str) -> str:
        if key not in BUSINESS_SETTING_META:
            raise ValueError(f"未知配置项: {key}")
        meta = BUSINESS_SETTING_META[key]
        typ = meta["type"]
        raw = str(value).strip()
        if raw == "" or raw.lower() in ("nan", "none", "null"):
            raise ValueError(f"{meta['label']}不能为空")
        try:
            if typ == "int":
                n = int(float(raw))  # 允许 "15.0"
                if n < meta["min"] or n > meta["max"]:
                    raise ValueError(f"{meta['label']}须在 {meta['min']}~{meta['max']} 之间")
                return str(n)
            if typ == "float":
                f = float(raw)
                if f < meta["min"] or f > meta["max"]:
                    raise ValueError(f"{meta['label']}须在 {meta['min']}~{meta['max']} 之间")
                return str(round(f, 4))
        except ValueError as e:
            if "须在" in str(e) or "不能为空" in str(e):
                raise
            raise ValueError(f"{meta['label']}格式无效") from e
        return raw

    @classmethod
    async def _apply_side_effects(cls, db: AsyncSession, key: str, normalized: str) -> None:
        if key != "visitor_data_retention_days":
            return
        from app.core.response import now_local

        days = int(normalized)
        new_expiry_cap = now_local() + timedelta(days=days)
        await db.execute(
            update(VisitorEvent)
            .where(VisitorEvent.expires_at > new_expiry_cap)
            .values(expires_at=new_expiry_cap)
        )

    @classmethod
    async def list_business(cls, db: AsyncSession) -> list[dict]:
        await cls.ensure_seeded(db)
        settings = get_settings()
        raw = await cls._load_raw(db)
        items = []
        for key, meta in BUSINESS_SETTING_META.items():
            val = raw.get(key, cls._env_default(key, settings))
            items.append(
                {
                    "key": key,
                    "value": val,
                    "label": meta["label"],
                    "unit": meta.get("unit", ""),
                    "type": meta["type"],
                    "min": meta.get("min"),
                    "max": meta.get("max"),
                    "step": meta.get("step"),
                    "description": meta.get("description"),
                    "editable": True,
                }
            )
        return items

    @classmethod
    async def update_business(cls, db: AsyncSession, updates: dict[str, str | int | float]) -> list[dict]:
        if not updates:
            raise ValueError("未提交任何配置项")
        unknown = [k for k in updates if k not in BUSINESS_SETTING_META]
        if unknown:
            raise ValueError(f"不可修改的配置项: {', '.join(unknown)}")

        await cls.ensure_seeded(db)
        changed: dict[str, str] = {}
        for key, val in updates.items():
            normalized = cls._validate_value(key, str(val))
            row = await db.scalar(select(SystemSetting).where(SystemSetting.setting_key == key))
            if row:
                if row.setting_value == normalized:
                    continue
                row.setting_value = normalized
            else:
                meta = BUSINESS_SETTING_META[key]
                new_row = SystemSetting(
                    setting_key=key,
                    setting_value=normalized,
                    description=meta.get("description"),
                )
                if await cls._is_sqlite(db):
                    new_row.id = await cls._sqlite_next_id(db)
                db.add(new_row)
            changed[key] = normalized
            await cls._apply_side_effects(db, key, normalized)

        await db.flush()
        await cls.invalidate_cache()
        logger.info("Business settings updated: %s", changed)
        return await cls.list_business(db)

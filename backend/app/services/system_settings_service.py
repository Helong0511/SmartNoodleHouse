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
# 类型：int / float / bool / string / enum（enum 须带 choices）
# group：前端「系统设置」分页分类

BUSINESS_SETTING_GROUPS: list[dict[str, str]] = [
    {"id": "order", "label": "订单与厨房", "description": "支付超时、厨房自动出餐、大厅叫号屏等堂食履约参数"},
    {"id": "welcome", "label": "迎宾与人脸", "description": "迎宾冷却、人脸匹配与访客保留"},
    {"id": "member", "label": "会员与优惠", "description": "新客赠券等会员相关"},
    {"id": "miniprogram", "label": "小程序与 H5", "description": "点餐页展示、隐私能力、订阅模板、扫码域名与小程序版本"},
    {"id": "delivery", "label": "外卖", "description": "自动接单与开发兜底门店（生产多店须关闭兜底）"},
    {"id": "print", "label": "厨房打印", "description": "打印总开关、模式、IP/端口、份数与票面样式"},
    {"id": "security", "label": "安全与网络", "description": "Token 有效期、Webhook 白名单、可信反代"},
    {"id": "system", "label": "系统通用", "description": "时区、上传限制与扩展名"},
]

BUSINESS_SETTING_META: dict[str, dict[str, Any]] = {
    "order_payment_timeout_minutes": {
        "group": "order",
        "label": "订单支付超时",
        "unit": "分钟",
        "type": "int",
        "min": 1,
        "max": 1440,
        "description": "未支付订单自动关闭时间",
    },
    "kitchen_auto_complete_minutes": {
        "group": "order",
        "label": "厨房自动出餐",
        "unit": "分钟",
        "type": "int",
        "min": 0,
        "max": 1440,
        "description": "支付后厨房 KDS 未处理超过该时间则自动完成出餐；0 表示关闭",
    },
    "enable_customer_display": {
        "group": "order",
        "label": "大厅叫号屏",
        "unit": "",
        "type": "bool",
        "default": False,
        "store_override": True,
        "description": "开启后可用 /display 顾客叫号屏；关闭时 API/推送不可用；无屏门店可关；可按门店覆盖",
    },
    "visitor_data_retention_days": {
        "group": "welcome",
        "label": "访客数据保留",
        "unit": "天",
        "type": "int",
        "min": 1,
        "max": 365,
        "description": "匿名访客事件保留天数；缩短时会同步压缩已有记录的过期时间",
    },
    "operation_log_retention_days": {
        "group": "system",
        "label": "操作日志保留",
        "unit": "天",
        "type": "int",
        "min": 7,
        "max": 3650,
        "description": "后台操作日志自动清理周期",
    },
    "welcome_cooldown_seconds": {
        "group": "welcome",
        "label": "迎宾冷却时间",
        "unit": "秒",
        "type": "int",
        "min": 0,
        "max": 3600,
        "description": "同一顾客两次迎宾的最小间隔",
    },
    "face_match_threshold": {
        "group": "welcome",
        "label": "人脸匹配阈值",
        "unit": "",
        "type": "float",
        "min": 0.5,
        "max": 0.99,
        "step": 0.01,
        "description": "相似度达到该值才视为同一人（0.75 推荐）",
    },
    "face_confirm_frames": {
        "group": "welcome",
        "label": "人脸确认帧数",
        "unit": "帧",
        "type": "int",
        "min": 1,
        "max": 30,
        "description": "连续 N 帧匹配成功才触发迎宾",
    },
    "delivery_auto_confirm": {
        "group": "delivery",
        "label": "外卖自动接单",
        "unit": "",
        "type": "bool",
        "store_override": True,
        "description": "新外卖单是否自动向平台 confirm；关闭则本地 PENDING，需商家确认；可按门店覆盖",
    },
    "welcome_coupon_name": {
        "group": "member",
        "label": "新客赠券模板名",
        "unit": "",
        "type": "string",
        "max_length": 64,
        "description": "须与 coupon_templates.name 完全一致且启用；空则不发券",
    },
    "enable_alipay_avatar": {
        "group": "miniprogram",
        "label": "支付宝头像能力",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "经小程序 /miniprogram/config 下发；个人主体请关闭（小程序主体级，全店共用）",
    },
    "enable_alipay_nickname": {
        "group": "miniprogram",
        "label": "支付宝昵称能力",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "经小程序 /miniprogram/config 下发（小程序主体级，全店共用）",
    },
    "enable_alipay_phone_bind": {
        "group": "miniprogram",
        "label": "支付宝手机号绑定",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "个人主体小程序通常须关闭（小程序主体级，全店共用）",
    },
    "enable_wx_avatar": {
        "group": "miniprogram",
        "label": "微信头像能力",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "经小程序 /miniprogram/config 下发（小程序主体级，全店共用）",
    },
    "enable_wx_nickname": {
        "group": "miniprogram",
        "label": "微信昵称能力",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "经小程序 /miniprogram/config 下发（小程序主体级，全店共用）",
    },
    "enable_wx_phone_bind": {
        "group": "miniprogram",
        "label": "微信手机号绑定",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "个人主体小程序通常须关闭（小程序主体级，全店共用）",
    },
    "show_menu_search": {
        "group": "miniprogram",
        "label": "点餐页·搜索菜品",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "顾客端点餐页是否显示搜索框；可按门店覆盖",
    },
    "show_menu_smart_taste": {
        "group": "miniprogram",
        "label": "点餐页·智能口味",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "顾客端点餐页是否显示智能口味条；可按门店覆盖",
    },
    "show_menu_guess_you_like": {
        "group": "miniprogram",
        "label": "点餐页·猜你喜欢",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "顾客端点餐页是否显示猜你喜欢推荐条；可按门店覆盖",
    },
    "hide_member_empty_wallet": {
        "group": "miniprogram",
        "label": "会员中心·无券隐藏券区",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "开启后无可用优惠券/代金券时不显示对应区块；可按门店覆盖",
    },
    "kitchen_ticket_order_no": {
        "group": "print",
        "label": "厨房票订单号样式",
        "unit": "",
        "type": "enum",
        "choices": ["barcode", "text", "off"],
        "store_override": True,
        "description": "barcode=条码+单号；text=仅单号；off=不打印；可按门店覆盖",
    },
    "xpyun_print_copies": {
        "group": "print",
        "label": "小票打印份数",
        "unit": "份",
        "type": "int",
        "min": 1,
        "max": 5,
        "store_override": True,
        "description": "局域网/云打印出票份数；可按门店覆盖",
    },
    "xpyun_voice": {
        "group": "print",
        "label": "云打印语音",
        "unit": "",
        "type": "int",
        "min": 0,
        "max": 3,
        "store_override": True,
        "description": "仅芯烨云：0取消单 1静音 2来单 3退单；可按门店覆盖",
    },
    "max_upload_size_mb": {
        "group": "system",
        "label": "上传大小上限",
        "unit": "MB",
        "type": "int",
        "min": 1,
        "max": 50,
        "description": "单文件上传上限（商品图/头像等）；改后对新上传生效",
    },
    "jwt_expire_minutes": {
        "group": "security",
        "label": "员工 Token 有效期",
        "unit": "分钟",
        "type": "int",
        "min": 30,
        "max": 10080,
        "description": "仅影响新签发的员工 JWT",
    },
    "customer_jwt_expire_minutes": {
        "group": "security",
        "label": "顾客 Token 有效期",
        "unit": "分钟",
        "type": "int",
        "min": 30,
        "max": 10080,
        "description": "仅影响新签发的顾客 JWT",
    },
    "webhook_ip_allowlist": {
        "group": "security",
        "label": "Webhook IP 白名单",
        "unit": "",
        "type": "string",
        "max_length": 2000,
        "description": "支付/外卖回调允许的 IP/CIDR，逗号分隔；空=不限制",
    },
    "webhook_require_allowlist": {
        "group": "security",
        "label": "强制 Webhook 白名单",
        "unit": "",
        "type": "bool",
        "store_override": False,
        "description": "生产环境白名单为空时是否直接拒绝回调（全局安全策略，不可按门店覆盖）",
    },
    "trusted_proxy_ips": {
        "group": "security",
        "label": "可信反代 IP",
        "unit": "",
        "type": "string",
        "max_length": 2000,
        "description": "仅这些直连 peer 可信任 X-Real-IP / X-Forwarded-For",
    },
    "h5_public_base_url": {
        "group": "miniprogram",
        "label": "H5 对外域名",
        "unit": "",
        "type": "string",
        "max_length": 255,
        "description": "桌台一码通扫 / 顾客 H5 基址，如 https://order.example.com",
    },
    "dev_lan_host": {
        "group": "miniprogram",
        "label": "开发机局域网 IP",
        "unit": "",
        "type": "string",
        "max_length": 128,
        "description": "开发环境把 localhost 改写成真机可访问地址；生产可留空",
    },
    "mini_program_env_version": {
        "group": "miniprogram",
        "label": "小程序码版本",
        "unit": "",
        "type": "enum",
        "choices": ["auto", "develop", "trial", "release"],
        "description": "auto=按环境推断；桌台码/URL Link 的 env_version",
    },
    "xpyun_enabled": {
        "group": "print",
        "label": "厨房打印总开关",
        "unit": "",
        "type": "bool",
        "store_override": True,
        "description": "关闭后不再出制作单（历史字段名 XPYUN_ENABLED）；可按门店覆盖",
    },
    "printer_mode": {
        "group": "print",
        "label": "打印机模式",
        "unit": "",
        "type": "enum",
        "choices": ["local", "xpyun"],
        "store_override": True,
        "description": "local=局域网 ESC/POS；xpyun=芯烨云；可按门店覆盖",
    },
    "printer_host": {
        "group": "print",
        "label": "打印机 IP",
        "unit": "",
        "type": "string",
        "max_length": 128,
        "store_override": True,
        "description": "局域网模式全局默认；可按门店覆盖",
    },
    "printer_port": {
        "group": "print",
        "label": "打印机端口",
        "unit": "",
        "type": "int",
        "min": 1,
        "max": 65535,
        "store_override": True,
        "description": "局域网 ESC/POS 端口，默认 9100；可按门店覆盖",
    },
    "wechat_subscribe_template_order_ready": {
        "group": "miniprogram",
        "label": "微信出餐订阅模板",
        "unit": "",
        "type": "string",
        "max_length": 128,
        "description": "公众平台订阅消息模板 ID；空则不下发",
    },
    "allowed_image_extensions": {
        "group": "system",
        "label": "允许的图片扩展名",
        "unit": "",
        "type": "string",
        "max_length": 128,
        "description": "逗号分隔，如 jpg,jpeg,png,webp,gif",
    },
    "app_timezone": {
        "group": "system",
        "label": "业务时区",
        "unit": "",
        "type": "string",
        "max_length": 64,
        "description": "如 Asia/Shanghai；影响订单时间展示与超时计算",
    },
    "delivery_env_fallback_store_id": {
        "group": "delivery",
        "label": "外卖 .env 兜底门店",
        "unit": "",
        "type": "int",
        "min": 0,
        "max": 999999,
        "description": "无绑定时允许用 .env Token 的本系统门店 ID；多店生产必须 0",
    },
    "preference_scene_enabled": {
        "group": "system",
        "label": "口味场景加权",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "推荐时按季节/同星期同餐段微调口味排序；可按门店覆盖",
    },
    "weather_pref_enabled": {
        "group": "system",
        "label": "天气口味加权",
        "unit": "",
        "type": "bool",
        "default": True,
        "store_override": True,
        "description": "门店配置经纬度后，按实时气温/雨天微调口味推荐；可按门店覆盖",
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
        meta = BUSINESS_SETTING_META.get(key) or {}
        if meta.get("type") == "bool":
            # 无 Settings 字段时优先用 META.default，避免把本应开启的项种成 false
            fallback = bool(meta.get("default", False))
            if val is None and "default" in meta:
                return "true" if fallback else "false"
            return "true" if cls._parse_bool(val, fallback) else "false"
        if key == "mini_program_env_version":
            raw = (str(val).strip().lower() if val is not None else "")
            return raw if raw in ("develop", "trial", "release") else "auto"
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
    def peek_cached(cls, key: str) -> str | None:
        """同步窥视本进程缓存（未预热则返回 None，调用方回退 .env）。"""
        if cls._cache is not None and key in cls._cache:
            return cls._cache[key]
        return None

    @classmethod
    def resolved_int_sync(cls, key: str, fallback: int) -> int:
        raw = cls.peek_cached(key)
        if raw is None:
            return fallback
        try:
            return int(raw)
        except (TypeError, ValueError):
            return fallback

    @classmethod
    def resolved_bool_sync(cls, key: str, fallback: bool) -> bool:
        raw = cls.peek_cached(key)
        if raw is None:
            return fallback
        return cls._parse_bool(raw, fallback)

    @classmethod
    def resolved_str_sync(cls, key: str, fallback: str) -> str:
        raw = cls.peek_cached(key)
        return fallback if raw is None else str(raw)

    @classmethod
    def resolved_mini_program_env_version_sync(cls) -> str:
        settings = get_settings()
        raw = cls.resolved_str_sync(
            "mini_program_env_version",
            (settings.mini_program_env_version or "").strip() or "auto",
        ).strip().lower()
        if raw in ("develop", "trial", "release"):
            return raw
        return "release" if settings.is_production else "develop"

    @classmethod
    async def touch_cache(cls, db: AsyncSession | None = None) -> None:
        """请求路径轻量刷新：Redis 版本未变且在 TTL 内则无 DB 查询。"""
        if db is not None:
            await cls._load_raw(db)
            return
        now = time.monotonic()
        remote_ver = await cls._remote_version()
        if (
            cls._cache is not None
            and cls._cache_ver == remote_ver
            and now - cls._cache_at < cls.CACHE_TTL
        ):
            return
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await cls._load_raw(session)

    @classmethod
    async def get_float(cls, db: AsyncSession, key: str, fallback: float) -> float:
        try:
            return float(await cls.get_value(db, key, fallback))
        except (TypeError, ValueError):
            return fallback

    @classmethod
    def _parse_bool(cls, raw: str | bool | int | None, fallback: bool = False) -> bool:
        if isinstance(raw, bool):
            return raw
        if raw is None:
            return fallback
        s = str(raw).strip().lower()
        if s in ("1", "true", "yes", "on", "y"):
            return True
        if s in ("0", "false", "no", "off", "n", ""):
            return False
        return fallback

    @classmethod
    async def get_bool(cls, db: AsyncSession, key: str, fallback: bool) -> bool:
        return cls._parse_bool(await cls.get_value(db, key, fallback), fallback)

    @classmethod
    async def store_override_map(cls, db: AsyncSession, key: str) -> dict[int, str]:
        """门店覆盖：store_id → raw value。"""
        from app.models import StoreSetting

        rows = (
            await db.execute(select(StoreSetting).where(StoreSetting.setting_key == key))
        ).scalars().all()
        return {int(r.store_id): r.setting_value for r in rows}

    @classmethod
    async def effective_setting(
        cls,
        db: AsyncSession,
        key: str,
        *,
        store_id: int | None = None,
        fallback: str | int | float | None = None,
    ) -> str:
        """
        业务读参入口：门店 store_settings 覆盖优先，否则全局 system_settings，再否则 fallback/.env。
        """
        if store_id is not None:
            from app.models import StoreSetting

            ov = await db.scalar(
                select(StoreSetting.setting_value).where(
                    StoreSetting.store_id == int(store_id),
                    StoreSetting.setting_key == key,
                )
            )
            if ov is not None and str(ov).strip() != "":
                return str(ov)
        fb = fallback if fallback is not None else cls._env_default(key)
        return await cls.get_value(db, key, fb)

    @classmethod
    async def effective_int(
        cls,
        db: AsyncSession,
        key: str,
        fallback: int,
        *,
        store_id: int | None = None,
    ) -> int:
        try:
            return int(await cls.effective_setting(db, key, store_id=store_id, fallback=fallback))
        except (TypeError, ValueError):
            return fallback

    @classmethod
    async def effective_float(
        cls,
        db: AsyncSession,
        key: str,
        fallback: float,
        *,
        store_id: int | None = None,
    ) -> float:
        try:
            return float(await cls.effective_setting(db, key, store_id=store_id, fallback=fallback))
        except (TypeError, ValueError):
            return fallback

    @classmethod
    async def effective_bool(
        cls,
        db: AsyncSession,
        key: str,
        fallback: bool,
        *,
        store_id: int | None = None,
    ) -> bool:
        return cls._parse_bool(
            await cls.effective_setting(db, key, store_id=store_id, fallback=fallback),
            fallback,
        )

    @classmethod
    def _validate_value(cls, key: str, value: str) -> str:
        if key not in BUSINESS_SETTING_META:
            raise ValueError(f"未知配置项: {key}")
        meta = BUSINESS_SETTING_META[key]
        typ = meta["type"]
        raw = str(value).strip()
        if typ == "bool":
            low = raw.lower()
            if low in ("1", "true", "yes", "on", "y"):
                return "true"
            if low in ("0", "false", "no", "off", "n"):
                return "false"
            raise ValueError(f"{meta['label']}须为 true/false")
        if typ == "string":
            # 允许空串（如关闭新客券 / 白名单）
            max_len = int(meta.get("max_length") or 256)
            if len(raw) > max_len:
                raise ValueError(f"{meta['label']}最多 {max_len} 字符")
            if key == "app_timezone" and raw:
                try:
                    from zoneinfo import ZoneInfo

                    ZoneInfo(raw)
                except Exception as e:
                    raise ValueError(f"{meta['label']}无效: {raw}") from e
            if key == "allowed_image_extensions" and raw:
                parts = [p.strip().lower().lstrip(".") for p in raw.split(",") if p.strip()]
                if not parts:
                    raise ValueError(f"{meta['label']}不能为空列表")
                return ",".join(parts)
            if key == "h5_public_base_url" and raw:
                if not (raw.startswith("http://") or raw.startswith("https://")):
                    raise ValueError(f"{meta['label']}须以 http:// 或 https:// 开头")
            return raw
        if typ == "enum":
            choices = [str(c) for c in (meta.get("choices") or [])]
            if raw.lower() in ("nan", "none", "null"):
                raise ValueError(f"{meta['label']}不能为空")
            # mini_program：空串视为 auto
            if key == "mini_program_env_version" and raw == "":
                return "auto"
            if raw not in choices:
                raise ValueError(f"{meta['label']}须为: {', '.join(choices)}")
            if key == "mini_program_env_version" and raw in ("develop", "trial"):
                from app.core.config import get_settings

                if get_settings().is_production:
                    raise ValueError("生产环境不可将小程序环境设为 develop/trial")
            return raw
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
    def allows_store_override(cls, key: str) -> bool:
        """是否允许写入 store_settings 覆盖。

        - META 显式 store_override 优先
        - 未声明时：布尔开关默认可覆盖，其它类型默认不可（须显式打开）
        """
        meta = BUSINESS_SETTING_META.get(key) or {}
        if "store_override" in meta:
            return bool(meta["store_override"])
        return meta.get("type") == "bool"

    @classmethod
    def group_meta_map(cls) -> dict[str, dict[str, str]]:
        return {g["id"]: g for g in BUSINESS_SETTING_GROUPS}

    @classmethod
    async def list_business(cls, db: AsyncSession) -> list[dict]:
        await cls.ensure_seeded(db)
        settings = get_settings()
        raw = await cls._load_raw(db)
        groups = cls.group_meta_map()
        items = []
        for key, meta in BUSINESS_SETTING_META.items():
            val = raw.get(key, cls._env_default(key, settings))
            gid = meta.get("group") or "system"
            gmeta = groups.get(gid) or {"id": gid, "label": gid, "description": ""}
            item: dict[str, Any] = {
                "key": key,
                "value": val,
                "label": meta["label"],
                "unit": meta.get("unit", ""),
                "type": meta["type"],
                "min": meta.get("min"),
                "max": meta.get("max"),
                "step": meta.get("step"),
                "max_length": meta.get("max_length"),
                "choices": meta.get("choices"),
                "description": meta.get("description"),
                "group": gid,
                "group_label": gmeta.get("label", gid),
                "group_description": gmeta.get("description", ""),
                "store_override": cls.allows_store_override(key),
                "editable": True,
            }
            if meta["type"] == "bool":
                item["bool_value"] = cls._parse_bool(val, False)
            items.append(item)
        return items

    @classmethod
    async def list_business_grouped(cls, db: AsyncSession) -> list[dict]:
        """按 BUSINESS_SETTING_GROUPS 顺序返回分组 + items。"""
        items = await cls.list_business(db)
        by_group: dict[str, list[dict]] = {}
        for it in items:
            by_group.setdefault(it["group"], []).append(it)
        result = []
        for g in BUSINESS_SETTING_GROUPS:
            result.append(
                {
                    "id": g["id"],
                    "label": g["label"],
                    "description": g.get("description", ""),
                    "items": by_group.get(g["id"], []),
                }
            )
        # 兜底：META 中出现未登记的 group
        known = {g["id"] for g in BUSINESS_SETTING_GROUPS}
        for gid, gitems in by_group.items():
            if gid not in known:
                result.append({"id": gid, "label": gid, "description": "", "items": gitems})
        return result

    @classmethod
    async def update_business(cls, db: AsyncSession, updates: dict[str, str | int | float | bool]) -> list[dict]:
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

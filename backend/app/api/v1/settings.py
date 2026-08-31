import json
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from sqlalchemy import select, text

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.response import success
from app.models import OperationLog, Store, StoreSetting
from app.schemas.settings import BusinessSettingsUpdate, StoreUpdate
from app.services.store_scope import assert_store_access, require_bound_store
from app.services.system_settings_service import BUSINESS_SETTING_META, SystemSettingsService
from pydantic import BaseModel, Field

router = APIRouter(prefix="/settings", tags=["系统设置"])


class StoreCreate(BaseModel):
    store_no: str = Field(..., min_length=1, max_length=40)
    name: str = Field(..., min_length=1, max_length=100)
    address: str | None = None
    phone: str | None = None


class StoreBusinessUpdate(BaseModel):
    updates: dict[str, str | int | float] = Field(default_factory=dict)


@router.get("/stores/options", dependencies=[require_permission("order:read")])
async def list_store_options(request: Request, db: DbSession, user: CurrentUser):
    """员工可选门店列表：超管看全部营业中门店；门店账号仅本店。"""
    effective = require_bound_store(user, None)
    q = select(Store).where(Store.status == 1).order_by(Store.id)
    if effective is not None:
        q = q.where(Store.id == effective)
    rows = list((await db.execute(q)).scalars().all())
    return success(
        {
            "items": [
                {"id": s.id, "store_no": s.store_no, "name": s.name, "address": s.address, "phone": s.phone}
                for s in rows
            ],
            "bound_store_id": user.store_id,
            "is_super_admin": user.role.code == "SUPER_ADMIN",
        },
        request=request,
    )


@router.post("/stores", dependencies=[require_permission("system:manage")])
async def create_store(data: StoreCreate, request: Request, db: DbSession, user: CurrentUser):
    """新建门店（仅超管/系统管理权限）。"""
    no = data.store_no.strip()
    exists = await db.scalar(select(Store.id).where(Store.store_no == no).limit(1))
    if exists:
        raise AppException(40070, f"门店编号「{no}」已存在")
    store = Store(
        store_no=no,
        name=data.name.strip(),
        address=(data.address or "").strip() or None,
        phone=(data.phone or "").strip() or None,
        status=1,
    )
    db.add(store)
    await db.flush()
    return success(
        {"id": store.id, "store_no": store.store_no, "name": store.name, "status": store.status},
        request=request,
    )


def _mask_secret(value: str, *, show_tail: int = 0) -> str:
    """生产侧仅显示「已配置」；开发可看尾部便于排查。"""
    if not value or not str(value).strip():
        return ""
    s = str(value).strip()
    settings = get_settings()
    if settings.is_production or show_tail <= 0:
        return "****已配置****"
    if len(s) <= show_tail + 2:
        return "****"
    return s[:2] + "****" + s[-show_tail:]


def _mask_db_url(url: str) -> str:
    if not url:
        return "(未配置)"
    try:
        u = urlparse(url.replace("+aiomysql", "").replace("+pymysql", ""))
        host = u.hostname or "?"
        port = f":{u.port}" if u.port else ""
        db = (u.path or "/").lstrip("/") or "?"
        user = u.username or "?"
        return f"{user}@{host}{port}/{db}"
    except Exception:
        return "****已配置****"


def _configured(val: str) -> bool:
    return bool(val and str(val).strip())


def _env_item(key: str, value: str, desc: str, *, sensitive: bool = False, restart: bool = True) -> dict:
    display = _mask_secret(value, show_tail=4) if sensitive and _configured(value) else (value or "")
    return {
        "key": key,
        "value": display,
        "configured": _configured(value),
        "sensitive": sensitive,
        "description": desc,
        "restart_required": restart,
    }


def _build_env_groups(settings) -> list[dict]:
    recognition = "1" if settings.enable_recognition_worker else "0"
    return [
        {
            "title": "应用基础",
            "description": "应用名称、环境、端口等，修改后需重启后端",
            "items": [
                _env_item("APP_ENV", settings.app_env, "development / production；生产会校验密钥与 mock 开关"),
                _env_item("APP_NAME", settings.app_name, "应用名称"),
                _env_item("APP_HOST", settings.app_host, "监听地址"),
                _env_item("APP_PORT", str(settings.app_port), "监听端口"),
                _env_item("APP_DEBUG", str(settings.app_debug).lower(), "调试模式"),
                _env_item("APP_TIMEZONE", settings.app_timezone, "业务时区"),
                _env_item("CORS_ORIGINS", settings.cors_origins, "允许跨域的前端地址，逗号分隔"),
                _env_item("VITE_API_BASE_URL", "(前端 .env)", "前端 API 地址，见 frontend/.env 或根 .env"),
            ],
        },
        {
            "title": "数据库与缓存",
            "items": [
                {
                    "key": "DATABASE_URL",
                    "value": _mask_db_url(settings.database_url),
                    "configured": _configured(settings.database_url),
                    "sensitive": True,
                    "description": "MySQL 连接串；密码特殊字符需 URL 编码",
                    "restart_required": True,
                },
                _env_item("REDIS_URL", _mask_db_url(settings.redis_url), "Redis 连接；WebSocket/限流/迎宾冷却/设置缓存版本依赖", sensitive=True),
            ],
        },
        {
            "title": "安全与认证",
            "items": [
                _env_item("JWT_SECRET", settings.jwt_secret, "员工 JWT 密钥，至少 32 字符", sensitive=True),
                _env_item("JWT_ALGORITHM", settings.jwt_algorithm, "JWT 算法"),
                _env_item("JWT_EXPIRE_MINUTES", str(settings.jwt_expire_minutes), "Token 有效期（分钟）"),
                _env_item("FACE_ENCRYPTION_KEY", settings.face_encryption_key, "人脸向量加密密钥，至少 32 字符", sensitive=True),
                _env_item("WEBHOOK_IP_ALLOWLIST", settings.webhook_ip_allowlist, "支付/外卖回调 IP 白名单，CIDR，逗号分隔"),
                _env_item("WEBHOOK_REQUIRE_ALLOWLIST", str(settings.webhook_require_allowlist).lower(), "是否强制校验 Webhook IP"),
                _env_item("ENABLE_RATE_LIMIT", str(settings.enable_rate_limit).lower(), "API 限流开关"),
            ],
        },
        {
            "title": "开发调试开关",
            "description": "生产环境 APP_ENV=production 时，mock 开关会被启动校验拒绝",
            "items": [
                _env_item("ALLOW_PAYMENT_MOCK", str(settings.allow_payment_mock).lower(), "假支付（仅开发）；未配商户号时 development 也会自动 mock 并入账"),
                _env_item("ALLOW_AUTH_MOCK", str(settings.allow_auth_mock).lower(), "强制 mock 小程序登录"),
                _env_item("ALLOW_DELIVERY_MOCK", str(settings.allow_delivery_mock).lower(), "外卖同步 mock 成功"),
            ],
        },
        {
            "title": "微信小程序",
            "description": "须与 miniprogram-wechat/project.config.json 的 appid 一致；留空时开发环境可自动 mock",
            "items": [
                _env_item("WECHAT_APP_ID", settings.wechat_app_id, "小程序 AppID"),
                _env_item("WECHAT_APP_SECRET", settings.wechat_app_secret, "小程序 AppSecret", sensitive=True),
                _env_item("WECHAT_MCH_ID", settings.wechat_mch_id, "微信支付商户号"),
                _env_item("WECHAT_API_V3_KEY", settings.wechat_api_v3_key, "APIv3 密钥", sensitive=True),
                _env_item("WECHAT_CERT_SERIAL_NO", settings.wechat_cert_serial_no, "商户证书序列号"),
                _env_item("WECHAT_PRIVATE_KEY_PATH", settings.wechat_private_key_path, "商户私钥 PEM 路径"),
                _env_item("WECHAT_NOTIFY_URL", settings.wechat_notify_url, "支付回调 URL"),
                _env_item(
                    "WECHAT_REFUND_NOTIFY_URL",
                    settings.wechat_refund_notify_url or settings.wechat_notify_url,
                    "退款回调 URL；空则复用支付回调",
                ),
                _env_item("WECHAT_PLATFORM_PUBLIC_KEY", settings.wechat_platform_public_key, "平台公钥（回调验签）", sensitive=True),
                _env_item("WECHAT_PLATFORM_CERT_PATH", settings.wechat_platform_cert_path, "平台证书路径"),
            ],
        },
        {
            "title": "支付宝",
            "items": [
                _env_item("ALIPAY_APP_ID", settings.alipay_app_id, "小程序/应用 AppID"),
                _env_item("ALIPAY_PRIVATE_KEY", settings.alipay_private_key, "应用私钥 PEM", sensitive=True),
                _env_item("ALIPAY_PUBLIC_KEY", settings.alipay_public_key, "支付宝公钥 PEM", sensitive=True),
                _env_item("ALIPAY_GATEWAY", settings.alipay_gateway, "网关地址"),
                _env_item("ALIPAY_NOTIFY_URL", settings.alipay_notify_url, "支付回调 URL"),
                _env_item("ALIPAY_RETURN_URL", settings.alipay_return_url, "同步跳转 URL"),
            ],
        },
        {
            "title": "美团外卖",
            "items": [
                _env_item("MEITUAN_APP_ID", settings.meituan_app_id, "开放平台 AppID"),
                _env_item("MEITUAN_APP_SECRET", settings.meituan_app_secret, "AppSecret", sensitive=True),
                _env_item("MEITUAN_SHOP_ID", settings.meituan_shop_id, "门店 ID"),
                _env_item("MEITUAN_NOTIFY_URL", settings.meituan_notify_url, "订单推送回调 URL"),
                _env_item("MEITUAN_API_BASE", settings.meituan_api_base, "API 基址"),
            ],
        },
        {
            "title": "饿了么外卖",
            "items": [
                _env_item("ELEME_APP_ID", settings.eleme_app_id, "开放平台 AppID"),
                _env_item("ELEME_APP_SECRET", settings.eleme_app_secret, "AppSecret", sensitive=True),
                _env_item("ELEME_SHOP_ID", settings.eleme_shop_id, "门店 ID"),
                _env_item("ELEME_ACCESS_TOKEN", settings.eleme_access_token, "Access Token", sensitive=True),
                _env_item("ELEME_NOTIFY_URL", settings.eleme_notify_url, "订单推送回调 URL"),
                _env_item("ELEME_API_BASE", settings.eleme_api_base, "API 基址"),
            ],
        },
        {
            "title": "外卖业务",
            "items": [
                _env_item("DELIVERY_AUTO_CONFIRM", str(settings.delivery_auto_confirm).lower(), "新外卖单是否自动向平台 confirm"),
            ],
        },
        {
            "title": "文件与对象存储",
            "items": [
                _env_item("UPLOAD_DIR", settings.upload_dir, "本地上传目录"),
                _env_item("MAX_UPLOAD_SIZE_MB", str(settings.max_upload_size_mb), "单文件大小上限"),
                _env_item("ALLOWED_IMAGE_EXTENSIONS", settings.allowed_image_extensions, "允许的图片扩展名"),
                _env_item("STORAGE_BACKEND", settings.storage_backend, "local 或 s3"),
                _env_item("S3_ENDPOINT", settings.s3_endpoint, "S3/MinIO 端点"),
                _env_item("S3_ACCESS_KEY", settings.s3_access_key, "S3 Access Key", sensitive=True),
                _env_item("S3_SECRET_KEY", settings.s3_secret_key, "S3 Secret Key", sensitive=True),
                _env_item("S3_BUCKET", settings.s3_bucket, "Bucket 名称"),
                _env_item("S3_PUBLIC_BASE_URL", settings.s3_public_base_url, "对外访问域名"),
            ],
        },
        {
            "title": "迎宾 / TTS / 摄像头",
            "items": [
                _env_item("TTS_PROVIDER", settings.tts_provider, "TTS 提供商（local 等）"),
                _env_item("TTS_VOICE", settings.tts_voice, "语音音色"),
                _env_item("CAMERA_URL", settings.camera_url, "默认 RTSP（可在摄像头管理覆盖）", sensitive=True),
                _env_item("CAMERA_USERNAME", settings.camera_username, "摄像头默认用户名"),
                _env_item("CAMERA_PASSWORD", settings.camera_password, "摄像头默认密码", sensitive=True),
                _env_item("ENABLE_RECOGNITION_WORKER", recognition, "是否启动人脸识别 worker（leader 进程）"),
            ],
        },
        {
            "title": "多进程与日志",
            "items": [
                _env_item("PROCESS_ROLE", settings.process_role, "leader 跑定时任务/TTS/识别；其他实例设 worker"),
                _env_item("JSON_LOGS", str(settings.json_logs).lower(), "JSON 结构化日志"),
                _env_item("LOG_LEVEL", settings.log_level, "日志级别 DEBUG/INFO/WARNING/ERROR"),
            ],
        },
        {
            "title": "业务参数默认值（.env）",
            "description": "仅当 system_settings 表无对应键时作为种子默认值；正式运行以「业务参数」页为准",
            "items": [
                _env_item("ORDER_PAYMENT_TIMEOUT_MINUTES", str(settings.order_payment_timeout_minutes), "订单支付超时（分钟）", restart=False),
                _env_item("KITCHEN_AUTO_COMPLETE_MINUTES", str(settings.kitchen_auto_complete_minutes), "厨房 KDS 未处理自动出餐（分钟，0=关闭）", restart=False),
                _env_item("VISITOR_DATA_RETENTION_DAYS", str(settings.visitor_data_retention_days), "访客数据保留（天）", restart=False),
                _env_item("OPERATION_LOG_RETENTION_DAYS", str(settings.operation_log_retention_days), "操作日志保留（天）", restart=False),
                _env_item("WELCOME_COOLDOWN_SECONDS", str(settings.welcome_cooldown_seconds), "迎宾冷却（秒）", restart=False),
                _env_item("FACE_MATCH_THRESHOLD", str(settings.face_match_threshold), "人脸匹配阈值", restart=False),
                _env_item("FACE_CONFIRM_FRAMES", str(settings.face_confirm_frames), "人脸确认帧数", restart=False),
            ],
        },
    ]


async def _check_health(db: DbSession) -> dict:
    health = {"database": False, "redis": False, "settings_cache_ver": 0}
    try:
        await db.execute(text("SELECT 1"))
        health["database"] = True
    except Exception:
        pass
    try:
        from app.core.redis_client import get_redis
        from app.services.system_settings_service import REDIS_CACHE_VER_KEY

        r = await get_redis()
        await r.ping()
        health["redis"] = True
        health["settings_cache_ver"] = int(await r.get(REDIS_CACHE_VER_KEY) or 0)
    except Exception:
        pass
    return health


async def _write_audit(
    db: DbSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    target_type: str,
    target_id: int | None,
    detail: dict,
) -> None:
    db.add(
        OperationLog(
            user_id=user.id,
            action=action,
            module="settings",
            target_type=target_type,
            target_id=target_id,
            ip=request.client.host if request.client else None,
            user_agent=(request.headers.get("user-agent") or "")[:500],
            request_id=getattr(request.state, "request_id", None),
            detail=json.dumps(detail, ensure_ascii=False)[:4000],
        )
    )


@router.get("", dependencies=[require_permission("system:manage")])
async def get_settings_overview(request: Request, db: DbSession):
    settings = get_settings()
    stores = (await db.execute(select(Store).order_by(Store.id))).scalars().all()
    business = await SystemSettingsService.list_business(db)
    health = await _check_health(db)
    env_path = Path(__file__).resolve().parents[4] / ".env"
    return success(
        {
            "app": {
                "env": settings.app_env,
                "name": settings.app_name,
                "timezone": settings.app_timezone,
                "is_production": settings.is_production,
                "process_role": settings.process_role,
            },
            "health": health,
            "stores": [
                {
                    "id": s.id,
                    "store_no": s.store_no,
                    "name": s.name,
                    "address": s.address,
                    "phone": s.phone,
                    "status": s.status,
                }
                for s in stores
            ],
            "business": business,
            "env_groups": _build_env_groups(settings),
            "env_file_hint": str(env_path),
            "cache": {
                "ttl_seconds": SystemSettingsService.CACHE_TTL,
                "multi_worker_note": "多 worker 通过 Redis 版本号同步失效；无 Redis 时最长延迟约 TTL 秒",
            },
            "scripts": [
                {"name": "初始化管理员", "command": "python scripts/init_admin.py", "note": "首次部署后执行并设置强密码"},
                {"name": "数据库初始化", "command": "生产: 001_schema + 002_seed_prod + init_admin", "note": "开发用 002_seed_test.sql"},
                {"name": "启动后端", "command": "cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000", "note": "Windows PowerShell 同理"},
                {"name": "启动前端", "command": "cd frontend && npm run dev", "note": "http://localhost:5173"},
                {"name": "系统设置并发测试", "command": "pytest tests/test_settings_concurrency.py -q", "note": "高并发读写/校验"},
            ],
        },
        request=request,
    )


@router.get("/health", dependencies=[require_permission("system:manage")])
async def settings_health(request: Request, db: DbSession):
    return success(await _check_health(db), request=request)


@router.put("/business", dependencies=[require_permission("system:manage")])
async def update_business_settings(
    data: BusinessSettingsUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    try:
        items = await SystemSettingsService.update_business(db, data.settings)
    except ValueError as e:
        raise AppException(40010, str(e))
    await _write_audit(
        db,
        user=user,
        request=request,
        action="settings.business.update",
        target_type="system_settings",
        target_id=None,
        detail={"keys": list(data.settings.keys()), "values": {k: str(v) for k, v in data.settings.items()}},
    )
    await db.flush()
    return success(
        {"business": items},
        message="业务参数已保存；本进程立即生效，多实例通过 Redis 版本同步",
        request=request,
    )


@router.get("/stores/{store_id}/business", dependencies=[require_permission("system:manage")])
async def get_store_business(
    store_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """门店业务参数：全局默认 + 本店覆盖。"""
    assert_store_access(user, store_id)
    store = await db.get(Store, store_id)
    if not store:
        raise AppException(40011, "门店不存在")
    global_items = await SystemSettingsService.list_business(db)
    overrides = {
        r.setting_key: r.setting_value
        for r in (
            await db.execute(select(StoreSetting).where(StoreSetting.store_id == store_id))
        ).scalars()
    }
    items = []
    for it in global_items:
        key = it["key"]
        has_ov = key in overrides
        items.append(
            {
                **it,
                "global_value": it["value"],
                "value": overrides[key] if has_ov else it["value"],
                "has_override": has_ov,
            }
        )
    return success({"store_id": store_id, "store_name": store.name, "items": items}, request=request)


@router.put("/stores/{store_id}/business", dependencies=[require_permission("system:manage")])
async def update_store_business(
    store_id: int,
    data: StoreBusinessUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    assert_store_access(user, store_id)
    store = await db.get(Store, store_id)
    if not store:
        raise AppException(40011, "门店不存在")
    if not data.updates:
        raise AppException(40012, "未提交任何修改")
    unknown = [k for k in data.updates if k not in BUSINESS_SETTING_META]
    if unknown:
        raise AppException(40014, f"不可修改的配置项: {', '.join(unknown)}")

    existing = {
        r.setting_key: r
        for r in (
            await db.execute(select(StoreSetting).where(StoreSetting.store_id == store_id))
        ).scalars()
    }
    for key, val in data.updates.items():
        sval = str(val)
        if key in existing:
            existing[key].setting_value = sval
        else:
            db.add(StoreSetting(store_id=store_id, setting_key=key, setting_value=sval))
    await db.flush()
    return await get_store_business(store_id, request, db, user)


@router.delete("/stores/{store_id}/business/{key}", dependencies=[require_permission("system:manage")])
async def clear_store_business_key(
    store_id: int,
    key: str,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """清除本店覆盖，回退全局默认。"""
    assert_store_access(user, store_id)
    if key not in BUSINESS_SETTING_META:
        raise AppException(40014, f"未知配置项: {key}")
    from sqlalchemy import delete as sa_delete

    await db.execute(
        sa_delete(StoreSetting).where(StoreSetting.store_id == store_id, StoreSetting.setting_key == key)
    )
    await db.flush()
    return success({"cleared": True, "store_id": store_id, "key": key}, request=request)


@router.put("/stores/{store_id}", dependencies=[require_permission("system:manage")])
async def update_store(
    store_id: int,
    data: StoreUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    store = await db.get(Store, store_id)
    if not store:
        raise AppException(40011, "门店不存在")
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        raise AppException(40012, "未提交任何修改")
    if "name" in payload and (not payload["name"] or not str(payload["name"]).strip()):
        raise AppException(40013, "门店名称不能为空")
    before = {"name": store.name, "address": store.address, "phone": store.phone, "status": store.status}
    for k, v in payload.items():
        setattr(store, k, v)
    await _write_audit(
        db,
        user=user,
        request=request,
        action="settings.store.update",
        target_type="store",
        target_id=store.id,
        detail={"before": before, "after": payload},
    )
    await db.flush()
    return success(
        {"id": store.id, "name": store.name, "address": store.address, "phone": store.phone, "status": store.status},
        request=request,
    )

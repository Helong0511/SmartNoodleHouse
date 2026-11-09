from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.response import success
from app.models import Store
from app.schemas.settings import BusinessSettingsUpdate
from app.services.system_settings_service import (
    BUSINESS_SETTING_GROUPS,
    SystemSettingsService,
)

router = APIRouter(prefix="/settings", tags=["系统设置"])


def _mask_secret(value: str, *, show_tail: int = 0) -> str:
    """任何环境都不回显密钥片段；仅返回是否已配置。"""
    if not value or not str(value).strip():
        return ""
    return "****已配置****"


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
                _env_item("JWT_EXPIRE_MINUTES", str(settings.jwt_expire_minutes), "员工 Token 有效期种子（运行时看业务参数）", restart=False),
                _env_item("CUSTOMER_JWT_EXPIRE_MINUTES", str(settings.customer_jwt_expire_minutes), "顾客 Token 有效期种子（运行时看业务参数）", restart=False),
                _env_item("FACE_ENCRYPTION_KEY", settings.face_encryption_key, "人脸向量加密密钥，至少 32 字符", sensitive=True),
                _env_item("WEBHOOK_IP_ALLOWLIST", settings.webhook_ip_allowlist, "Webhook 白名单种子（运行时看业务参数）", restart=False),
                _env_item("WEBHOOK_REQUIRE_ALLOWLIST", str(settings.webhook_require_allowlist).lower(), "强制白名单种子（运行时看业务参数）", restart=False),
                _env_item("TRUSTED_PROXY_IPS", settings.trusted_proxy_ips, "可信反代种子（运行时看业务参数）", restart=False),
                _env_item("ENABLE_RATE_LIMIT", str(settings.enable_rate_limit).lower(), "API 限流开关（须重启）"),
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
            "description": "门店 Token 在「门店 → 门店信息 → 外卖绑定」；下列默认值仅作 system_settings 种子，正式以门店业务参数为准",
            "items": [
                _env_item(
                    "DELIVERY_AUTO_CONFIRM",
                    str(settings.delivery_auto_confirm).lower(),
                    "新外卖单自动接单（种子默认；运行时看门店业务参数）",
                    restart=False,
                ),
                _env_item(
                    "DELIVERY_ENV_FALLBACK_STORE_ID",
                    str(settings.delivery_env_fallback_store_id),
                    "无绑定时允许 .env Token 的门店 ID；多店生产须为 0（平台级）",
                    restart=False,
                ),
            ],
        },
        {
            "title": "文件与对象存储",
            "items": [
                _env_item("UPLOAD_DIR", settings.upload_dir, "本地上传目录"),
                _env_item(
                    "MAX_UPLOAD_SIZE_MB",
                    str(settings.max_upload_size_mb),
                    "上传上限种子默认（运行时看业务参数）",
                    restart=False,
                ),
                _env_item(
                    "ALLOWED_IMAGE_EXTENSIONS",
                    settings.allowed_image_extensions,
                    "允许的图片扩展名种子（运行时看业务参数）",
                    restart=False,
                ),
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
                _env_item(
                    "TTS_PROVIDER",
                    settings.tts_provider,
                    "TTS：edge=神经女声（需出网+ffmpeg）；local=本机 SAPI/espeak",
                ),
                _env_item(
                    "TTS_VOICE",
                    settings.tts_voice,
                    "音色：gentle_female/温柔女生/女生/男生，或 zh-CN-XiaoxiaoNeural",
                ),
                _env_item(
                    "TTS_RATE",
                    getattr(settings, "tts_rate", "-10%"),
                    "edge 语速（如 -10% 略慢更柔）",
                ),
                _env_item("CAMERA_URL", settings.camera_url, "默认 RTSP（可在摄像头管理覆盖）", sensitive=True),
                _env_item("CAMERA_USERNAME", settings.camera_username, "摄像头默认用户名"),
                _env_item("CAMERA_PASSWORD", settings.camera_password, "摄像头默认密码", sensitive=True),
                _env_item("ENABLE_RECOGNITION_WORKER", recognition, "是否启动人脸识别 worker（leader 进程）"),
                _env_item(
                    "FACE_LIVENESS_MODE",
                    settings.face_liveness_mode,
                    "人脸注册活体：challenge=动作挑战；none=仅 basic 粗检",
                ),
                _env_item(
                    "FACE_LIVENESS_CHALLENGE_TIMEOUT",
                    str(settings.face_liveness_challenge_timeout),
                    "活体挑战有效期（秒）",
                ),
                _env_item(
                    "UNKNOWN_VISITOR_CAPTURE_ENABLED",
                    str(settings.unknown_visitor_capture_enabled).lower(),
                    "未知访客抓拍（默认开启；图存 visitors/年/月/日/）",
                    restart=False,
                ),
            ],
        },
        {
            "title": "多进程与日志",
            "items": [
                _env_item("PROCESS_ROLE", settings.process_role, "leader 跑定时任务/TTS/识别；其他实例设 worker"),
                _env_item("JSON_LOGS", str(settings.json_logs).lower(), "JSON 结构化日志"),
                _env_item("LOG_LEVEL", settings.log_level, "日志级别 DEBUG/INFO/WARNING/ERROR"),
                _env_item("LOG_DIR", settings.log_dir, "文件日志根目录（相对仓库根或绝对路径）"),
            ],
        },
        {
            "title": "业务参数默认值（.env）",
            "description": "仅当 system_settings 表无对应键时作为种子；门店可覆盖项以「门店信息 → 业务参数」为准，平台项以「系统设置」平台分类为准",
            "items": [
                _env_item("ORDER_PAYMENT_TIMEOUT_MINUTES", str(settings.order_payment_timeout_minutes), "订单支付超时（分钟）", restart=False),
                _env_item("KITCHEN_AUTO_COMPLETE_MINUTES", str(settings.kitchen_auto_complete_minutes), "厨房 KDS 未处理自动出餐（分钟，0=关闭）", restart=False),
                _env_item("VISITOR_DATA_RETENTION_DAYS", str(settings.visitor_data_retention_days), "访客数据保留（天）", restart=False),
                _env_item("OPERATION_LOG_RETENTION_DAYS", str(settings.operation_log_retention_days), "操作日志保留（天）", restart=False),
                _env_item("WELCOME_COOLDOWN_SECONDS", str(settings.welcome_cooldown_seconds), "迎宾冷却（秒）", restart=False),
                _env_item("FACE_MATCH_THRESHOLD", str(settings.face_match_threshold), "人脸匹配阈值", restart=False),
                _env_item("FACE_CONFIRM_FRAMES", str(settings.face_confirm_frames), "人脸确认帧数", restart=False),
                _env_item("WELCOME_COUPON_NAME", settings.welcome_coupon_name, "新客赠券模板名", restart=False),
                _env_item("ENABLE_ALIPAY_AVATAR", str(settings.enable_alipay_avatar).lower(), "支付宝头像能力", restart=False),
                _env_item("ENABLE_ALIPAY_NICKNAME", str(settings.enable_alipay_nickname).lower(), "支付宝昵称能力", restart=False),
                _env_item("ENABLE_ALIPAY_PHONE_BIND", str(settings.enable_alipay_phone_bind).lower(), "支付宝手机号绑定", restart=False),
                _env_item("ENABLE_WX_AVATAR", str(settings.enable_wx_avatar).lower(), "微信头像能力", restart=False),
                _env_item("ENABLE_WX_NICKNAME", str(settings.enable_wx_nickname).lower(), "微信昵称能力", restart=False),
                _env_item("ENABLE_WX_PHONE_BIND", str(settings.enable_wx_phone_bind).lower(), "微信手机号绑定", restart=False),
                _env_item(
                    "ENABLE_FACE_WELCOME",
                    str(settings.enable_face_welcome).lower(),
                    "小程序人脸迎宾入口（默认关闭）",
                    restart=False,
                ),
                _env_item("KITCHEN_TICKET_ORDER_NO", settings.kitchen_ticket_order_no, "厨房票订单号样式 barcode/text/off", restart=False),
                _env_item("XPYUN_PRINT_COPIES", str(settings.xpyun_print_copies), "小票打印份数", restart=False),
                _env_item("XPYUN_VOICE", str(settings.xpyun_voice), "云打印语音 0~3", restart=False),
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


from app.api.settings_common import write_audit as _write_audit


@router.get("", dependencies=[require_permission("system:manage")])
async def get_settings_overview(request: Request, db: DbSession):
    settings = get_settings()
    stores = (await db.execute(select(Store).order_by(Store.id))).scalars().all()
    business = await SystemSettingsService.list_business(db)
    business_groups = await SystemSettingsService.list_business_grouped(db)
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
                    "latitude": float(s.latitude) if s.latitude is not None else None,
                    "longitude": float(s.longitude) if s.longitude is not None else None,
                    "status": s.status,
                }
                for s in stores
            ],
            "business": business,
            "business_groups": business_groups,
            "business_group_defs": BUSINESS_SETTING_GROUPS,
            "env_groups": _build_env_groups(settings),
            "env_file_hint": str(env_path),
            "cache": {
                "ttl_seconds": SystemSettingsService.CACHE_TTL,
                "multi_worker_note": "多 worker 通过 Redis 版本号同步失效；无 Redis 时最长延迟约 TTL 秒",
            },
            "scripts": [
                {"name": "初始化管理员", "command": "python scripts/初始化管理员.py", "note": "首次部署后执行并设置强密码"},
                {"name": "数据库初始化", "command": "struct.sql + data_sys.sql + data_custom.sql + 初始化管理员.py", "note": "全新库专用；struct.sql 会删库重建"},
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


@router.get("/scheduler-jobs", dependencies=[require_permission("system:manage")])
async def list_scheduler_jobs(request: Request, db: DbSession):
    """定时任务目录 + 覆盖启停/周期 + Leader 运行时 next_run。"""
    from app.main import scheduler
    from app.tasks.scheduler import describe_scheduler_status

    return success(await describe_scheduler_status(scheduler, db=db), request=request)


class SchedulerJobUpdate(BaseModel):
    # 三态：True=启用任务 / False=停用任务 / None=不改该项（部分更新语义）
    enabled: bool | None = None
    schedule: dict | None = None
    reset: bool = False


@router.put("/scheduler-jobs/{job_id}", dependencies=[require_permission("system:manage")])
async def update_scheduler_job(
    job_id: str,
    data: SchedulerJobUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """启停或修改周期；写入 system_settings，Leader 进程立即热更新。"""
    from app.core.leader_lock import is_leader_held
    from app.main import scheduler
    from app.services.scheduler_admin import (
        apply_job_to_scheduler,
        effective_schedule,
        load_overrides,
        save_overrides,
        validate_schedule,
    )
    from app.tasks.scheduler import TASK_CATALOG, describe_scheduler_status

    meta = next((m for m in TASK_CATALOG if m["id"] == job_id), None)
    if not meta:
        raise AppException(40410, f"未知定时任务: {job_id}")

    overrides = await load_overrides(db)
    current = dict(overrides.get(job_id) or {}) if isinstance(overrides.get(job_id), dict) else {}

    if data.reset:
        overrides.pop(job_id, None)
        await save_overrides(db, overrides)
        schedule = dict(meta.get("default_schedule") or {})
        enabled = True
        detail = {"job_id": job_id, "reset": True}
    else:
        if data.enabled is None and data.schedule is None:
            raise AppException(40010, "请提供 enabled 或 schedule，或 reset=true")
        try:
            if data.enabled is not None:
                current["enabled"] = bool(data.enabled)
            if data.schedule is not None:
                current["schedule"] = validate_schedule(data.schedule)
            # 补齐缺省字段
            if "enabled" not in current:
                current["enabled"] = True
            if "schedule" not in current:
                current["schedule"] = dict(meta.get("default_schedule") or {})
            else:
                current["schedule"] = validate_schedule(current["schedule"])
        except (ValueError, TypeError) as e:
            raise AppException(40010, str(e)) from e

        default_sch = dict(meta.get("default_schedule") or {})
        # 与默认完全一致则删除覆盖项
        if current.get("enabled", True) and current.get("schedule") == default_sch:
            overrides.pop(job_id, None)
        else:
            # 周期等于默认时可不落 schedule，只保留 enabled
            if current.get("schedule") == default_sch:
                current = {"enabled": bool(current.get("enabled", True))}
            overrides[job_id] = current
        await save_overrides(db, overrides)
        ov_after = overrides.get(job_id)
        if isinstance(ov_after, dict):
            enabled = bool(ov_after.get("enabled", True))
            schedule = effective_schedule(meta, ov_after)
        else:
            enabled = True
            schedule = dict(meta.get("default_schedule") or {})
        detail = {"job_id": job_id, "enabled": enabled, "schedule": schedule}

    # operation_logs.target_id 为 bigint，任务 ID 为字符串，只能放 detail.job_id
    await _write_audit(
        db,
        user=user,
        request=request,
        action="settings.scheduler.update",
        target_type="scheduler_job",
        target_id=None,
        detail=detail,
    )
    await db.commit()

    # 任何进程保存后通知 Leader 热应用（本进程若是 Leader 也会收到，幂等）
    try:
        from app.services.scheduler_admin import notify_overrides_changed

        await notify_overrides_changed(job_id=job_id)
    except Exception:
        logger = __import__("logging").getLogger(__name__)
        logger.exception("通知 Leader 热更新失败 job_id=%s", job_id)

    applied = False
    apply_error = None
    if getattr(scheduler, "running", False) and is_leader_held():
        try:
            apply_job_to_scheduler(scheduler, job_id, schedule=schedule, enabled=enabled)
            applied = True
        except Exception as e:
            apply_error = str(e)
            logger = __import__("logging").getLogger(__name__)
            logger.exception("热更新定时任务失败 job_id=%s", job_id)

    payload = await describe_scheduler_status(scheduler, db=db)
    payload["applied_runtime"] = applied
    if apply_error:
        payload["apply_error"] = apply_error
    msg = "已保存"
    if applied:
        msg += "并已热更新本进程调度器"
    elif not getattr(scheduler, "running", False) or not is_leader_held():
        msg += "（已落库并通知 Leader；持锁进程将热生效）"
    return success(payload, message=msg, request=request)


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
    groups = await SystemSettingsService.list_business_grouped(db)
    return success(
        {"business": items, "business_groups": groups},
        message="业务参数已保存；本进程立即生效，多实例通过 Redis 版本同步",
        request=request,
    )

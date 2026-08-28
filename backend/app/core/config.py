from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 与仓库根目录均可放 .env（README 约定在根目录）
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_PROJECT_ROOT = _BACKEND_ROOT.parent
_ENV_CANDIDATES = (
    _PROJECT_ROOT / ".env",
    _BACKEND_ROOT / ".env",
    Path(".env"),
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=tuple(str(p) for p in _ENV_CANDIDATES),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "SmartNoodleHouse"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False
    app_timezone: str = "Asia/Shanghai"

    database_url: str = "mysql+aiomysql://root:password@127.0.0.1:3306/smart_noodle_house?charset=utf8mb4"
    redis_url: str = "redis://127.0.0.1:6379/0"

    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    upload_dir: str = "uploads"
    max_upload_size_mb: int = 5
    allowed_image_extensions: str = "jpg,jpeg,png,webp,gif"

    wechat_app_id: str = ""
    wechat_app_secret: str = ""
    wechat_mch_id: str = ""
    wechat_api_v3_key: str = ""
    wechat_cert_serial_no: str = ""
    wechat_private_key_path: str = "certs/wechat_apiclient_key.pem"
    wechat_notify_url: str = ""
    # 退款结果回调；留空则复用 WECHAT_NOTIFY_URL（同一入口按 event_type 分流）
    wechat_refund_notify_url: str = ""
    # 小程序订阅消息：出餐/待取餐提醒（微信公众平台申请模板后填入）
    wechat_subscribe_template_order_ready: str = ""

    alipay_app_id: str = ""
    alipay_private_key: str = ""
    alipay_public_key: str = ""
    alipay_gateway: str = "https://openapi.alipay.com/gateway.do"
    alipay_notify_url: str = ""
    alipay_return_url: str = ""

    # 美团外卖开放平台（waimaiopen）
    meituan_app_id: str = ""
    meituan_app_secret: str = ""
    meituan_shop_id: str = ""
    meituan_notify_url: str = ""
    meituan_api_base: str = "https://waimaiopen.meituan.com"

    # 饿了么商家开放平台（open-api.shop.ele.me）
    eleme_app_id: str = ""
    eleme_app_secret: str = ""
    eleme_shop_id: str = ""
    eleme_notify_url: str = ""
    eleme_access_token: str = ""
    eleme_api_base: str = "https://open-api.shop.ele.me/api/v1"

    tts_provider: str = "local"
    tts_voice: str = "zh-CN"

    camera_url: str = ""
    camera_username: str = ""
    camera_password: str = ""

    face_encryption_key: str = ""

    # 微信平台公钥/证书 PEM（回调 Wechatpay-Signature 验签必填；生产强制）
    wechat_platform_public_key: str = ""
    wechat_platform_cert_path: str = "certs/wechat_platform_cert.pem"

    # 仅开发环境允许：假支付 / mock 小程序登录（生产启动校验会拒绝）
    allow_payment_mock: bool = False
    allow_auth_mock: bool = False
    # 开发机未装 InsightFace 时可用 OpenCV 弱特征兜底；生产禁止
    allow_opencv_face_fallback: bool = False
    # 外卖新单是否自动向平台 confirm（默认 false：本地 PENDING，需商家确认）
    delivery_auto_confirm: bool = False
    enable_rate_limit: bool = True
    # 未配置平台密钥时，同步接口是否允许返回 mock 成功（生产必须 false）
    allow_delivery_mock: bool = False

    # 对象存储：local | s3
    storage_backend: str = "local"
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_public_base_url: str = ""

    # Webhook IP 白名单，逗号分隔，支持 CIDR；空=不限制
    webhook_ip_allowlist: str = ""
    webhook_require_allowlist: bool = False

    # 多进程角色：仅一个实例应为 leader（调度/TTS/摄像头/识别）
    process_role: str = "leader"
    # 是否在 leader 进程启动人脸识别 worker（须走 settings 读 .env，勿用 os.getenv）
    enable_recognition_worker: bool = True

    # 结构化 JSON 日志
    json_logs: bool = False
    log_level: str = "INFO"

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # 顾客 H5/一码通扫对外域名（如 https://order.example.com）；留空则用 CORS 首项
    h5_public_base_url: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in ("production", "prod")

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in ("development", "dev", "local")

    order_payment_timeout_minutes: int = 15
    visitor_data_retention_days: int = 7
    operation_log_retention_days: int = 90
    welcome_cooldown_seconds: int = 60
    face_match_threshold: float = 0.75
    face_confirm_frames: int = 3

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_extensions(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_image_extensions.split(",")}


@lru_cache
def get_settings() -> Settings:
    return Settings()
